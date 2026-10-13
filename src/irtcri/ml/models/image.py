"""Image encoders (thermal: 1 channel, RGB: 3 channels)."""

from __future__ import annotations

import warnings

import torch
from torch import nn


class SimpleCNN(nn.Module):
    """Small CNN for smoke tests and very small datasets."""

    def __init__(self, in_ch: int, width: int = 16):
        super().__init__()
        def block(i, o):
            return nn.Sequential(nn.Conv2d(i, o, 3, padding=1), nn.BatchNorm2d(o), nn.ReLU(inplace=True),
                                 nn.MaxPool2d(2))
        self.features = nn.Sequential(block(in_ch, width), block(width, width * 2), block(width * 2, width * 4),
                                      nn.Conv2d(width * 4, width * 8, 3, padding=1), nn.ReLU(inplace=True))
        self.out_dim = width * 8
        self.pool = nn.AdaptiveAvgPool2d(1)

    def forward(self, x):
        return self.pool(self.features(x)).flatten(1)


def _adapt_first_conv(conv: nn.Conv2d, in_ch: int) -> nn.Conv2d:
    if in_ch == conv.in_channels:
        return conv
    new = nn.Conv2d(in_ch, conv.out_channels, conv.kernel_size, conv.stride, conv.padding, bias=conv.bias is not None)
    with torch.no_grad():
        new.weight.copy_(conv.weight.mean(dim=1, keepdim=True).repeat(1, in_ch, 1, 1))
        if conv.bias is not None:
            new.bias.copy_(conv.bias)
    return new


class TorchvisionEncoder(nn.Module):
    def __init__(self, name: str, in_ch: int, pretrained: bool):
        super().__init__()
        import torchvision.models as tvm
        weights = None
        self.pretrained_loaded = False
        if pretrained:
            weights = {"resnet18": "IMAGENET1K_V1", "resnet34": "IMAGENET1K_V1", "resnet50": "IMAGENET1K_V2",
                       "efficientnet_b0": "IMAGENET1K_V1", "vit_b_16": "IMAGENET1K_V1"}[name]
        try:
            m = getattr(tvm, name)(weights=weights)
            self.pretrained_loaded = weights is not None
        except Exception as e:   # offline: fall back to random init, recorded in run metadata
            warnings.warn(f"could not load pretrained weights for {name} ({e}); using random initialisation",
                          stacklevel=2)
            m = getattr(tvm, name)(weights=None)
        self.name = name
        if name.startswith("resnet"):
            m.conv1 = _adapt_first_conv(m.conv1, in_ch)
            self.out_dim = m.fc.in_features
            m.fc = nn.Identity()
            self.cam_layer = m.layer4
        elif name.startswith("efficientnet"):
            m.features[0][0] = _adapt_first_conv(m.features[0][0], in_ch)
            self.out_dim = m.classifier[1].in_features
            m.classifier = nn.Identity()
            self.cam_layer = m.features[-1]
        elif name == "vit_b_16":
            m.conv_proj = _adapt_first_conv(m.conv_proj, in_ch)
            self.out_dim = m.heads.head.in_features
            m.heads = nn.Identity()
            self.cam_layer = None   # Grad-CAM not defined; use attention-based methods (future work)
        self.m = m

    def forward(self, x):
        return self.m(x)


def make_encoder(backbone: str, in_ch: int, pretrained: bool) -> nn.Module:
    if backbone == "simple_cnn":
        enc = SimpleCNN(in_ch)
        enc.cam_layer = enc.features
        enc.pretrained_loaded = False
        return enc
    return TorchvisionEncoder(backbone, in_ch, pretrained)
