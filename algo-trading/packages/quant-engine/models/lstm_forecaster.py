"""
models/lstm_forecaster.py — PyTorch LSTM sequence model for return forecasting.

Architecture
------------
A two-layer bidirectional LSTM followed by a fully-connected head that maps the
final hidden state to a scalar signal in [-1, +1]:

    Input: (batch, seq_len, input_dim)
        ↓
    LayerNorm
        ↓
    LSTM(hidden_dim, num_layers, bidirectional, dropout)
        ↓
    Take final timestep hidden state → (batch, hidden_dim * 2 if bidir)
        ↓
    Linear → ReLU → Dropout → Linear → Tanh
        ↓
    Output: (batch, 1)  ∈ [-1, +1]

Training loop
-------------
* Loss: MSE on the (clipped) next-period log-return target.
* Optimizer: AdamW with weight decay.
* LR schedule: ReduceLROnPlateau (patience=5).
* Early stopping: patience=10 epochs on validation loss.
* Data loader: uses walk-forward splits — no shuffling across time.

Confidence score
----------------
We use the absolute value of the output signal, calibrated against a rolling
validation window.  High-magnitude predictions correspond to high confidence.
A sigmoid temperature scaling maps |signal| → [0, 1].

Usage
-----
::

    from features.pipeline import FeaturePipeline
    from models.lstm_forecaster import LSTMForecaster

    pipeline = FeaturePipeline(store=None, ...)
    feature_names = pipeline.feature_names()

    model = LSTMForecaster(
        input_dim=len(feature_names),
        seq_len=30,          # 30-bar lookback window
        hidden_dim=128,
        num_layers=2,
        dropout=0.2,
    )
    model.train(X_train, y_train, X_val=X_val, y_val=y_val)
    output = model.predict(X_recent)   # X_recent shape: (seq_len, input_dim)
    print(output.signal, output.confidence)
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

try:
    import torch
    import torch.nn as nn
    from torch.utils.data import DataLoader, TensorDataset
    _TORCH_AVAILABLE = True
except ImportError:
    _TORCH_AVAILABLE = False
    logger.warning("PyTorch not installed — LSTMForecaster unavailable. Install with: pip install torch")

from models.base import BaseSignalModel, SignalOutput


# ---------------------------------------------------------------------------
# PyTorch module (defined only when torch is available)
# ---------------------------------------------------------------------------

if _TORCH_AVAILABLE:
    class _LSTMModule(nn.Module):
        """
        Internal PyTorch LSTM module.

        Parameters
        ----------
        input_dim : int
            Number of input features per timestep.
        hidden_dim : int
            LSTM hidden state size.
        num_layers : int
            Number of stacked LSTM layers.
        dropout : float
            Dropout probability applied between LSTM layers and before output.
        bidirectional : bool
            If True, use a bidirectional LSTM (doubles effective hidden_dim).
        """

        def __init__(
            self,
            input_dim: int,
            hidden_dim: int,
            num_layers: int,
            dropout: float,
            bidirectional: bool,
        ) -> None:
            super().__init__()
            self.norm = nn.LayerNorm(input_dim)
            self.lstm = nn.LSTM(
                input_size=input_dim,
                hidden_size=hidden_dim,
                num_layers=num_layers,
                dropout=dropout if num_layers > 1 else 0.0,
                bidirectional=bidirectional,
                batch_first=True,
            )
            effective_dim = hidden_dim * (2 if bidirectional else 1)
            self.head = nn.Sequential(
                nn.Linear(effective_dim, effective_dim // 2),
                nn.ReLU(),
                nn.Dropout(dropout),
                nn.Linear(effective_dim // 2, 1),
                nn.Tanh(),
            )

        def forward(self, x: "torch.Tensor") -> "torch.Tensor":
            # x: (batch, seq_len, input_dim)
            x = self.norm(x)
            out, _ = self.lstm(x)         # out: (batch, seq_len, hidden * dirs)
            last = out[:, -1, :]          # take final timestep
            return self.head(last).squeeze(-1)   # (batch,)


# ---------------------------------------------------------------------------
# LSTMForecaster
# ---------------------------------------------------------------------------

class LSTMForecaster(BaseSignalModel):
    """
    PyTorch LSTM sequence model for next-period return prediction.

    Parameters
    ----------
    input_dim : int
        Number of input features.
    seq_len : int
        Number of past bars fed as input (sliding window size).
    hidden_dim : int
        LSTM hidden state dimension.
    num_layers : int
        Number of stacked LSTM layers.
    dropout : float
        Dropout probability.
    bidirectional : bool
        Use bidirectional LSTM.
    lr : float
        Initial learning rate for AdamW.
    weight_decay : float
        L2 regularisation for AdamW.
    max_epochs : int
        Maximum training epochs.
    batch_size : int
        Mini-batch size.
    early_stopping_patience : int
        Stop after this many epochs without val-loss improvement.
    lr_patience : int
        ReduceLROnPlateau patience.
    device : str or None
        ``'cpu'``, ``'cuda'``, or ``'mps'``.  If None, auto-detects.
    """

    def __init__(
        self,
        input_dim: int,
        seq_len: int = 30,
        hidden_dim: int = 128,
        num_layers: int = 2,
        dropout: float = 0.2,
        bidirectional: bool = True,
        lr: float = 1e-3,
        weight_decay: float = 1e-4,
        max_epochs: int = 100,
        batch_size: int = 64,
        early_stopping_patience: int = 10,
        lr_patience: int = 5,
        device: str | None = None,
    ) -> None:
        super().__init__(input_dim)
        if not _TORCH_AVAILABLE:
            raise ImportError("PyTorch is required for LSTMForecaster. Install with: pip install torch")

        self.seq_len = seq_len
        self.hidden_dim = hidden_dim
        self.num_layers = num_layers
        self.dropout = dropout
        self.bidirectional = bidirectional
        self.lr = lr
        self.weight_decay = weight_decay
        self.max_epochs = max_epochs
        self.batch_size = batch_size
        self.early_stopping_patience = early_stopping_patience
        self.lr_patience = lr_patience

        if device is None:
            if torch.cuda.is_available():
                device = "cuda"
            elif torch.backends.mps.is_available():
                device = "mps"
            else:
                device = "cpu"
        self.device = torch.device(device)

        self._net = _LSTMModule(input_dim, hidden_dim, num_layers, dropout, bidirectional).to(self.device)
        self._val_std: float = 1.0  # used for confidence calibration

    @property
    def model_id(self) -> str:
        return "lstm_forecaster"

    # ── Training ─────────────────────────────────────────────────────────────

    def train(  # type: ignore[override]
        self,
        X: pd.DataFrame | np.ndarray,
        y: pd.Series | np.ndarray,
        X_val: pd.DataFrame | np.ndarray | None = None,
        y_val: pd.Series | np.ndarray | None = None,
        **kwargs: Any,
    ) -> None:
        """
        Fit the LSTM on walk-forward training data.

        Parameters
        ----------
        X : array (n_train, input_dim)
            Feature matrix in chronological order.
        y : array (n_train,)
            Target: next-period log return, clipped to [-1, +1].
        X_val, y_val : optional validation arrays for early stopping.
        """
        X_arr = self._to_numpy(X)
        y_arr = self._to_numpy(y).astype(np.float32)

        # Build sliding-window sequences
        X_seq, y_seq = self._make_sequences(X_arr, y_arr)

        train_loader = self._make_loader(X_seq, y_seq, shuffle=False)

        val_loader = None
        if X_val is not None and y_val is not None:
            Xv = self._to_numpy(X_val)
            yv = self._to_numpy(y_val).astype(np.float32)
            Xv_seq, yv_seq = self._make_sequences(Xv, yv)
            val_loader = self._make_loader(Xv_seq, yv_seq, shuffle=False)
            # Store std for confidence calibration
            self._val_std = float(np.std(yv_seq)) + 1e-6

        optimizer = torch.optim.AdamW(
            self._net.parameters(), lr=self.lr, weight_decay=self.weight_decay
        )
        scheduler = torch.optim.lr_scheduler.ReduceLROnPlateau(
            optimizer, patience=self.lr_patience, factor=0.5, min_lr=1e-6
        )
        criterion = nn.MSELoss()

        best_val_loss = float("inf")
        patience_counter = 0
        best_state = None

        self._net.train()
        for epoch in range(self.max_epochs):
            train_loss = self._run_epoch(train_loader, criterion, optimizer)

            if val_loader is not None:
                val_loss = self._run_epoch(val_loader, criterion, optimizer=None)
                scheduler.step(val_loss)

                if val_loss < best_val_loss - 1e-6:
                    best_val_loss = val_loss
                    patience_counter = 0
                    best_state = {k: v.clone() for k, v in self._net.state_dict().items()}
                else:
                    patience_counter += 1

                if patience_counter >= self.early_stopping_patience:
                    logger.info("LSTM early stop at epoch %d (val_loss=%.6f)", epoch, val_loss)
                    break

                if (epoch + 1) % 10 == 0:
                    logger.debug(
                        "LSTM epoch %d/%d — train=%.6f  val=%.6f",
                        epoch + 1, self.max_epochs, train_loss, val_loss,
                    )
            else:
                scheduler.step(train_loss)
                if (epoch + 1) % 10 == 0:
                    logger.debug("LSTM epoch %d/%d — train=%.6f", epoch + 1, self.max_epochs, train_loss)

        if best_state is not None:
            self._net.load_state_dict(best_state)

        self._is_trained = True
        self._net.eval()

    def _run_epoch(
        self,
        loader: "DataLoader",
        criterion: "nn.Module",
        optimizer: "torch.optim.Optimizer | None",
    ) -> float:
        total_loss = 0.0
        n = 0
        training = optimizer is not None
        self._net.train(training)
        ctx = torch.no_grad() if not training else torch.enable_grad()
        with ctx:
            for X_batch, y_batch in loader:
                X_batch = X_batch.to(self.device)
                y_batch = y_batch.to(self.device)
                preds = self._net(X_batch)
                loss = criterion(preds, y_batch)
                if training:
                    optimizer.zero_grad()
                    loss.backward()
                    nn.utils.clip_grad_norm_(self._net.parameters(), max_norm=1.0)
                    optimizer.step()
                total_loss += loss.item() * len(y_batch)
                n += len(y_batch)
        return total_loss / max(n, 1)

    # ── Prediction ────────────────────────────────────────────────────────────

    def predict(self, X: pd.DataFrame | np.ndarray, **kwargs: Any) -> SignalOutput:
        """
        Predict signal for the latest bar.

        Parameters
        ----------
        X : array, shape (seq_len, input_dim) or (n >= seq_len, input_dim)
            Feature sequence.  If longer than seq_len, uses the last seq_len rows.
        """
        self._assert_trained()
        X_arr = self._to_numpy(X).astype(np.float32)

        # Use the last seq_len bars
        if len(X_arr) >= self.seq_len:
            window = X_arr[-self.seq_len:]
        else:
            # Pad at the front with the first row
            pad = np.tile(X_arr[0], (self.seq_len - len(X_arr), 1))
            window = np.vstack([pad, X_arr])

        tensor = torch.tensor(window[np.newaxis], device=self.device)  # (1, seq_len, input_dim)
        self._net.eval()
        with torch.no_grad():
            raw = self._net(tensor).item()

        signal = float(np.clip(raw, -1.0, 1.0))
        # Confidence: calibrated against validation std.
        # High |signal| relative to typical predictions → high confidence.
        confidence = float(1.0 / (1.0 + np.exp(-abs(raw) / (self._val_std + 1e-6))))
        confidence = float(np.clip(confidence, 0.0, 1.0))

        return SignalOutput(signal=signal, confidence=confidence, model_id=self.model_id)

    # ── Serialisation ─────────────────────────────────────────────────────────

    def save(self, path: str | Path) -> None:
        """Save model weights and hyperparameters to *path* (directory)."""
        path = Path(path)
        path.mkdir(parents=True, exist_ok=True)
        torch.save(self._net.state_dict(), path / "weights.pt")
        meta = {
            "input_dim": self.input_dim,
            "seq_len": self.seq_len,
            "hidden_dim": self.hidden_dim,
            "num_layers": self.num_layers,
            "dropout": self.dropout,
            "bidirectional": self.bidirectional,
            "val_std": self._val_std,
        }
        (path / "meta.json").write_text(json.dumps(meta, indent=2))
        logger.info("LSTMForecaster saved to %s", path)

    @classmethod
    def load(cls, path: str | Path) -> "LSTMForecaster":
        """Restore a saved LSTMForecaster from *path*."""
        path = Path(path)
        meta = json.loads((path / "meta.json").read_text())
        model = cls(
            input_dim=meta["input_dim"],
            seq_len=meta["seq_len"],
            hidden_dim=meta["hidden_dim"],
            num_layers=meta["num_layers"],
            dropout=meta["dropout"],
            bidirectional=meta["bidirectional"],
        )
        model._val_std = meta.get("val_std", 1.0)
        model._net.load_state_dict(torch.load(path / "weights.pt", map_location=model.device, weights_only=True))
        model._net.eval()
        model._is_trained = True
        return model

    # ── Internal helpers ──────────────────────────────────────────────────────

    @staticmethod
    def _to_numpy(arr: pd.DataFrame | pd.Series | np.ndarray) -> np.ndarray:
        if isinstance(arr, (pd.DataFrame, pd.Series)):
            return arr.to_numpy(dtype=np.float32)
        return np.asarray(arr, dtype=np.float32)

    def _make_sequences(
        self, X: np.ndarray, y: np.ndarray
    ) -> tuple[np.ndarray, np.ndarray]:
        """Convert flat arrays to overlapping sliding-window sequences."""
        n = len(X) - self.seq_len
        if n <= 0:
            raise ValueError(
                f"Need at least seq_len+1={self.seq_len + 1} rows, got {len(X)}"
            )
        X_seq = np.stack([X[i : i + self.seq_len] for i in range(n)], axis=0)
        y_seq = y[self.seq_len:]
        return X_seq.astype(np.float32), y_seq.astype(np.float32)

    def _make_loader(
        self, X: np.ndarray, y: np.ndarray, shuffle: bool
    ) -> "DataLoader":
        dataset = TensorDataset(
            torch.tensor(X, dtype=torch.float32),
            torch.tensor(y, dtype=torch.float32),
        )
        return DataLoader(dataset, batch_size=self.batch_size, shuffle=shuffle)
