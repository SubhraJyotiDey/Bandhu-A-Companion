"""
model.py - Lightweight Depthwise Separable CNN (DS-CNN) Metric Backbone.

Designed for microcontrollers (ESP32, Raspberry Pi Zero 2W, STM32, Cortex-M4/M7).
Outputs a normalized 64-dimensional embedding vector on the unit hypersphere.
Cosine similarity can be directly computed via dot product: cos(u, v) = u . v
"""

import torch
import torch.nn as nn
import torch.nn.functional as F


class DepthwiseSeparableConv(nn.Module):
    def __init__(self, in_channels, out_channels, stride=1):
        super(DepthwiseSeparableConv, self).__init__()
        self.depthwise = nn.Conv2d(
            in_channels,
            in_channels,
            kernel_size=3,
            stride=stride,
            padding=1,
            groups=in_channels,
            bias=False,
        )
        self.bn_dw = nn.BatchNorm2d(in_channels)
        self.pointwise = nn.Conv2d(
            in_channels,
            out_channels,
            kernel_size=1,
            stride=1,
            padding=0,
            bias=False,
        )
        self.bn_pw = nn.BatchNorm2d(out_channels)
        self.relu = nn.ReLU()

    def forward(self, x):
        x = self.relu(self.bn_dw(self.depthwise(x)))
        x = self.relu(self.bn_pw(self.pointwise(x)))
        return x


class DSCNNBackbone(nn.Module):
    """
    Lightweight DS-CNN Feature Extractor for Edge Few-Shot Keyword Spotting.
    Input: (B, 1, 40, 49) representing (batch, channels, mel_bins, time_frames)
    Output: (B, embed_dim) normalized embedding vector (||z||_2 == 1.0)
    """

    def __init__(self, embed_dim=64, input_channels=1):
        super(DSCNNBackbone, self).__init__()
        self.embed_dim = embed_dim

        # Initial Standard Convolution: downsamples time and frequency
        self.init_conv = nn.Sequential(
            nn.Conv2d(
                input_channels,
                48,
                kernel_size=(5, 4),
                stride=(2, 2),
                padding=(2, 1),
                bias=False,
            ),
            nn.BatchNorm2d(48),
            nn.ReLU(),
        )

        # Depthwise Separable Conv Blocks
        self.blocks = nn.Sequential(
            DepthwiseSeparableConv(48, 48, stride=1),
            DepthwiseSeparableConv(48, 64, stride=(2, 2)),
            DepthwiseSeparableConv(64, 64, stride=1),
            DepthwiseSeparableConv(64, 96, stride=(2, 2)),
            DepthwiseSeparableConv(96, 96, stride=1),
        )

        # Global average pooling down to (B, 96, 1, 1)
        self.global_pool = nn.AdaptiveAvgPool2d((1, 1))

        # Linear projection to target embedding space
        self.fc = nn.Linear(96, embed_dim, bias=False)

    def forward(self, x):
        """
        Forward pass.
        Returns L2-normalized embedding vector of shape (B, embed_dim).
        """
        x = self.init_conv(x)
        x = self.blocks(x)
        x = self.global_pool(x)
        x = torch.flatten(x, 1)
        x = self.fc(x)

        # L2-normalize to unit hypersphere
        norm = torch.norm(x, p=2, dim=1, keepdim=True).clamp(min=1e-12)
        embeddings = x / norm
        return embeddings
