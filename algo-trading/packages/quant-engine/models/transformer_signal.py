"""
models/transformer_signal.py — Transformer encoder signal model.

Architecture
------------
A compact Transformer encoder that treats the feature time-series as a
sequence of token embeddings, attends over them with multi-head self-attention,
and projects the CLS-token representation to a scalar signal:

    Input: (batch, seq_len, input_dim)
        ↓
    Linear projection → d_model (token embedding)
        ↓
    Prepend learnable [CLS] token
        ↓
    Learnable positional encoding (seq_len + 1, d_model)
        ↓
    N × TransformerEncoderLayer (d_model, nhead, dim_feedforward, dropout)
        ↓
    Take [CLS] position output
        ↓
    LayerNorm → Linear → Tanh
        ↓
    Output: (batch,)  ∈ [-1, +1]

Why a CLS token?
----------------
The CLS (classification) token is a learnable token prepended to the sequence.
After attention layers, it aggregates a global representation of the entire
sequence.  This is the same trick used by BERT and Vision Transformers — the
model learns what aspects of the time-series are relevant for the output.

Learnable positional encoding
------------------------------
Unlike sinusoidal encoding (which is fixed), learnable positional embeddings
let the model discover its own ordering structure.  This is especially useful
for financial sequences where the recency of information is more important
than absolute position.

Usage
-----
::

    from models.transformer_signal import TransformerSignalModel

    model = TransformerSignalModel(
        input_dim=60,   # number of feature columns
        seq_len=20,     # 20-bar attention window
        d_model=64,
        nhead=4,
        num_layers=2,
    )
    model.train(X_train, y_train, X_val=X_val, y_val=y_val)
    signal_out = model.predict(X_recent)
"""

from __future__ import annotations

import json
import logging
import math
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
    logger.warning("PyTorch not installed — TransformerSignalModel unavailable.")

from models.base import BaseSignalModel, SignalOutput


# ---------------------------------------------------------------------------
# PyTorch module
# ---------------------------------------------------------------------------

if _TORCH_AVAILABLE:
    class _TransformerModule(nn.Module):
        """
        Transformer encoder with a learnable CLS token and learnable positional encoding.

        Parameters
        ----------
        input_dim : int
            Raw feature dimension.
        seq_len : int
            Sequence length (number of bars). Positional encoding has seq_len + 1 positions
            to account for the CLS token.
        d_model : int
            Internal embedding dimension (must be divisible by nhead).
        nhead : int
            Number of self-attention heads.
        num_layers : int
            Number of TransformerEncoderLayer blocks.
        dim_feedforward : int
            Feedforward expansion in each encoder layer.
        dropout : float
            Dropout probability in attention and feedforward layers.
        """

        def __init__(
            self,
            input_dim: int,
            seq_len: int,
            d_model: int,
            nhead: int,
            num_layers: int,
            dim_feedforward: int,
            dropout: float,
        ) -> None:
            super().__init__()

            # Project raw features into d_model space
            self.input_proj = nn.Linear(input_dim, d_model)

            # Learnable CLS token — one row that gets prepended to every sequence
            self.cls_token = nn.Parameter(torch.randn(1, 1, d_model) * 0.02)

            # Learnable positional encoding for seq_len bars + 1 CLS position
            self.pos_enc = nn.Parameter(torch.randn(1, seq_len + 1, d_model) * 0.02)

            encoder_layer = nn.TransformerEncoderLayer(
                d_model=d_model,
                nhead=nhead,
                dim_feedforward=dim_feedforward,
                dropout=dropout,
                batch_first=True,
                norm_first=True,   # Pre-LN: more stable than Post-LN
            )
            self.encoder = nn.TransformerEncoder(encoder_layer, num_layers=num_layers)

            self.head = nn.Sequential(
                nn.LayerNorm(d_model),
                nn.Linear(d_model, 1),
                nn.Tanh(),
            )

        def forward(self, x: "torch.Tensor") -> "torch.Tensor":
            # x: (batch, seq_len, input_dim)
            batch_size = x.size(0)

            # Project to embedding space
            x = self.input_proj(x)                                # (B, S, d_model)

            # Prepend CLS token
            cls = self.cls_token.expand(batch_size, -1, -1)       # (B, 1, d_model)
            x = torch.cat([cls, x], dim=1)                        # (B, S+1, d_model)

            # Add positional encoding
            x = x + self.pos_enc[:, : x.size(1), :]               # broadcast over batch

            # Transformer encoder
            x = self.encoder(x)                                    # (B, S+1, d_model)

            # Use CLS output as global representation
            cls_out = x[:, 0, :]                                   # (B, d_model)
            return self.head(cls_out).squeeze(-1)                  # (B,)


# ---------------------------------------------------------------------------
# TransformerSignalModel
# ---------------------------------------------------------------------------

class TransformerSignalModel(BaseSignalModel):
    """
    Transformer encoder for time-series signal generation.

    Parameters
    ----------
    input_dim : int
        Number of input features.
    seq_len : int
        Attention window length (number of past bars).
    d_model : int
        Embedding dimension.
    nhead : int
        Number of attention heads.  Must divide d_model evenly.
    num_layers : int
        Number of encoder layers.
    dim_feedforward : int
        Feedforward layer size.
    dropout : float
        Dropout rate.
    lr : float
        AdamW learning rate.
    weight_decay : float
        AdamW weight decay.
    max_epochs : int
        Max training epochs.
    batch_size : int
        Mini-batch size.
    early_stopping_patience : int
        Epochs without improvement before stopping.
    device : str or None
        Device string. Auto-detected if None.
    """

    def __init__(
        self,
        input_dim: int,
        seq_len: int = 20,
        d_model: int = 64,
        nhead: int = 4,
        num_layers: int = 2,
        dim_feedforward: int = 256,
        dropout: float = 0.1,
        lr: float = 1e-3,
        weight_decay: float = 1e-4,
        max_epochs: int = 100,
        batch_size: int = 64,
        early_stopping_patience: int = 10,
        device: str | None = None,
    ) -> None:
        super().__init__(input_dim)
        if not _TORCH_AVAILABLE:
            raise ImportError("PyTorch is required. Install with: pip install torch")

        if d_model % nhead != 0:
            raise ValueError(f"d_model ({d_model}) must be divisible by nhead ({nhead})")

        self.seq_len = seq_len
        self.d_model = d_model
        self.nhead = nhead
        self.num_layers = num_layers
        self.dim_feedforward = dim_feedforward
        self.dropout = dropout
        self.lr = lr
        self.weight_decay = weight_decay
        self.max_epochs = max_epochs
        self.batch_size = batch_size
        self.early_stopping_patience = early_stopping_patience

        if device is None:
            if torch.cuda.is_available():
                device = "cuda"
            elif torch.backends.mps.is_available():
                device = "mps"
            else:
                device = "cpu"
        self.device = torch.device(device)

        self._net = _TransformerModule(
            input_dim, seq_len, d_model, nhead, num_layers, dim_feedforward, dropout
        ).to(self.device)

    @property
    def model_id(self) -> str:
        return "transformer_signal"

    # ── Training ─────────────────────────────────────────────────────────────

    def train(  # type: ignore[override]
        self,
        X: pd.DataFrame | np.ndarray,
        y: pd.Series | np.ndarray,
        X_val: pd.DataFrame | np.ndarray | None = None,
        y_val: pd.Series | np.ndarray | None = None,
        **kwargs: Any,
    ) -> None:
        """Fit on walk-forward training data with optional early stopping."""
        X_arr = self._to_numpy(X)
        y_arr = self._to_numpy(y)
        X_seq, y_seq = self._make_sequences(X_arr, y_arr)
        train_loader = self._make_loader(X_seq, y_seq, shuffle=False)

        val_loader = None
        if X_val is not None and y_val is not None:
            Xv = self._to_numpy(X_val)
            yv = self._to_numpy(y_val)
            Xv_seq, yv_seq = self._make_sequences(Xv, yv)
            val_loader = self._make_loader(Xv_seq, yv_seq, shuffle=False)

        optimizer = torch.optim.AdamW(
            self._net.parameters(), lr=self.lr, weight_decay=self.weight_decay
        )
        # Cosine annealing restarts: good for Transformers (avoid LR plateau)
        scheduler = torch.optim.lr_scheduler.CosineAnnealingLR(
            optimizer, T_max=self.max_epochs, eta_min=1e-6
        )
        criterion = nn.MSELoss()

        best_val_loss = float("inf")
        patience_counter = 0
        best_state = None

        for epoch in range(self.max_epochs):
            self._net.train()
            train_loss = 0.0
            n = 0
            for Xb, yb in train_loader:
                Xb, yb = Xb.to(self.device), yb.to(self.device)
                pred = self._net(Xb)
                loss = criterion(pred, yb)
                optimizer.zero_grad()
                loss.backward()
                nn.utils.clip_grad_norm_(self._net.parameters(), 1.0)
                optimizer.step()
                train_loss += loss.item() * len(yb)
                n += len(yb)
            train_loss /= max(n, 1)
            scheduler.step()

            if val_loader is not None:
                self._net.eval()
                val_loss = 0.0
                nv = 0
                with torch.no_grad():
                    for Xb, yb in val_loader:
                        Xb, yb = Xb.to(self.device), yb.to(self.device)
                        pred = self._net(Xb)
                        val_loss += criterion(pred, yb).item() * len(yb)
                        nv += len(yb)
                val_loss /= max(nv, 1)

                if val_loss < best_val_loss - 1e-6:
                    best_val_loss = val_loss
                    patience_counter = 0
                    best_state = {k: v.clone() for k, v in self._net.state_dict().items()}
                else:
                    patience_counter += 1

                if patience_counter >= self.early_stopping_patience:
                    logger.info("Transformer early stop epoch %d (val=%.6f)", epoch, val_loss)
                    break

                if (epoch + 1) % 10 == 0:
                    logger.debug("Transformer %d/%d train=%.6f val=%.6f", epoch + 1, self.max_epochs, train_loss, val_loss)
            else:
                if (epoch + 1) % 10 == 0:
                    logger.debug("Transformer %d/%d train=%.6f", epoch + 1, self.max_epochs, train_loss)

        if best_state is not None:
            self._net.load_state_dict(best_state)
        self._net.eval()
        self._is_trained = True

    # ── Prediction ────────────────────────────────────────────────────────────

    def predict(self, X: pd.DataFrame | np.ndarray, **kwargs: Any) -> SignalOutput:
        """
        Predict signal.  X must have shape (>= seq_len, input_dim).
        Uses the last seq_len rows.
        """
        self._assert_trained()
        X_arr = self._to_numpy(X)
        if len(X_arr) >= self.seq_len:
            window = X_arr[-self.seq_len:]
        else:
            pad = np.tile(X_arr[0], (self.seq_len - len(X_arr), 1))
            window = np.vstack([pad, X_arr])

        tensor = torch.tensor(window[np.newaxis], dtype=torch.float32, device=self.device)
        self._net.eval()
        with torch.no_grad():
            raw = self._net(tensor).item()

        signal = float(np.clip(raw, -1.0, 1.0))
        # Confidence from |signal|: stronger predictions → higher confidence
        confidence = float(np.clip(abs(signal) * 1.5, 0.0, 1.0))
        return SignalOutput(signal=signal, confidence=confidence, model_id=self.model_id)

    # ── Serialisation ─────────────────────────────────────────────────────────

    def save(self, path: str | Path) -> None:
        path = Path(path)
        path.mkdir(parents=True, exist_ok=True)
        torch.save(self._net.state_dict(), path / "weights.pt")
        meta = {
            "input_dim": self.input_dim,
            "seq_len": self.seq_len,
            "d_model": self.d_model,
            "nhead": self.nhead,
            "num_layers": self.num_layers,
            "dim_feedforward": self.dim_feedforward,
            "dropout": self.dropout,
        }
        (path / "meta.json").write_text(json.dumps(meta, indent=2))
        logger.info("TransformerSignalModel saved to %s", path)

    @classmethod
    def load(cls, path: str | Path) -> "TransformerSignalModel":
        path = Path(path)
        meta = json.loads((path / "meta.json").read_text())
        model = cls(**meta)
        model._net.load_state_dict(
            torch.load(path / "weights.pt", map_location=model.device, weights_only=True)
        )
        model._net.eval()
        model._is_trained = True
        return model

    # ── Helpers ───────────────────────────────────────────────────────────────

    @staticmethod
    def _to_numpy(arr: Any) -> np.ndarray:
        if isinstance(arr, (pd.DataFrame, pd.Series)):
            return arr.to_numpy(dtype=np.float32)
        return np.asarray(arr, dtype=np.float32)

    def _make_sequences(self, X: np.ndarray, y: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        n = len(X) - self.seq_len
        if n <= 0:
            raise ValueError(f"Need >= seq_len+1={self.seq_len + 1} rows, got {len(X)}")
        X_seq = np.stack([X[i : i + self.seq_len] for i in range(n)])
        return X_seq.astype(np.float32), y[self.seq_len:].astype(np.float32)

    def _make_loader(self, X: np.ndarray, y: np.ndarray, shuffle: bool) -> "DataLoader":
        ds = TensorDataset(torch.tensor(X), torch.tensor(y))
        return DataLoader(ds, batch_size=self.batch_size, shuffle=shuffle)
