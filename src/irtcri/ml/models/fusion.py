"""Model families 1-5: image-only and late-fusion multimodal networks."""

from __future__ import annotations

import torch
from torch import nn

from irtcri.ml.models.image import make_encoder

IMAGENET_MEAN = torch.tensor([0.485, 0.456, 0.406]).view(1, 3, 1, 1)
IMAGENET_STD = torch.tensor([0.229, 0.224, 0.225]).view(1, 3, 1, 1)


class MultimodalNet(nn.Module):
    """Encoders per modality -> concatenated embeddings -> MLP head (late fusion).

    A missing RGB image is represented by a zero embedding plus a presence flag, so
    the model can be trained/evaluated on images without an RGB pair.
    """

    def __init__(self, modalities: list[str], backbone: str, pretrained: bool, n_clinical: int = 0,
                 clinical_hidden: int = 64, fusion_hidden: int = 128, dropout: float = 0.2):
        super().__init__()
        self.modalities = modalities
        dims = 0
        if "thermal" in modalities:
            self.thermal_enc = make_encoder(backbone, 1, pretrained)
            dims += self.thermal_enc.out_dim
        if "rgb" in modalities:
            self.rgb_enc = make_encoder(backbone, 3, pretrained)
            dims += self.rgb_enc.out_dim + 1
        if "clinical" in modalities:
            self.clin_enc = nn.Sequential(nn.Linear(n_clinical, clinical_hidden), nn.ReLU(), nn.Dropout(dropout),
                                          nn.Linear(clinical_hidden, clinical_hidden), nn.ReLU())
            dims += clinical_hidden
        self.head = nn.Sequential(nn.Dropout(dropout), nn.Linear(dims, fusion_hidden), nn.ReLU(),
                                  nn.Dropout(dropout), nn.Linear(fusion_hidden, 1))

    def forward(self, batch: dict) -> torch.Tensor:
        parts = []
        if "thermal" in self.modalities:
            parts.append(self.thermal_enc(batch["thermal"]))
        if "rgb" in self.modalities:
            x = (batch["rgb"] - IMAGENET_MEAN.to(batch["rgb"].device)) / IMAGENET_STD.to(batch["rgb"].device)
            present = batch["rgb_present"].float().view(-1, 1)
            parts += [self.rgb_enc(x) * present, present]
        if "clinical" in self.modalities:
            parts.append(self.clin_enc(batch["clinical"]))
        return self.head(torch.cat(parts, dim=1)).squeeze(1)
