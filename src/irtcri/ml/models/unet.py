"""Compact U-Net for insertion-site / inflamed-region segmentation on thermal images.

Use only if annotated masks/ROIs are available in sufficient number; its value
(e.g. for automated ROI features) must be shown against simple ROI baselines.
"""

from __future__ import annotations

import torch
from torch import nn


def _block(i: int, o: int) -> nn.Sequential:
    return nn.Sequential(nn.Conv2d(i, o, 3, padding=1), nn.BatchNorm2d(o), nn.ReLU(inplace=True),
                         nn.Conv2d(o, o, 3, padding=1), nn.BatchNorm2d(o), nn.ReLU(inplace=True))


class UNet(nn.Module):
    def __init__(self, in_ch: int = 1, n_classes: int = 1, base: int = 16, depth: int = 4):
        super().__init__()
        chs = [base * 2 ** i for i in range(depth)]
        self.downs = nn.ModuleList()
        c = in_ch
        for ch in chs:
            self.downs.append(_block(c, ch))
            c = ch
        self.pool = nn.MaxPool2d(2)
        self.bottleneck = _block(chs[-1], chs[-1] * 2)
        self.ups, self.dec = nn.ModuleList(), nn.ModuleList()
        c = chs[-1] * 2
        for ch in reversed(chs):
            self.ups.append(nn.ConvTranspose2d(c, ch, 2, stride=2))
            self.dec.append(_block(ch * 2, ch))
            c = ch
        self.out = nn.Conv2d(c, n_classes, 1)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        skips = []
        for d in self.downs:
            x = d(x)
            skips.append(x)
            x = self.pool(x)
        x = self.bottleneck(x)
        for up, dec, s in zip(self.ups, self.dec, reversed(skips), strict=True):
            x = up(x)
            x = dec(torch.cat([x, s], dim=1))
        return self.out(x)


def dice_loss(logits: torch.Tensor, target: torch.Tensor, eps: float = 1.0) -> torch.Tensor:
    p = torch.sigmoid(logits)
    inter = (p * target).sum(dim=(1, 2, 3))
    union = p.sum(dim=(1, 2, 3)) + target.sum(dim=(1, 2, 3))
    return (1 - (2 * inter + eps) / (union + eps)).mean()
