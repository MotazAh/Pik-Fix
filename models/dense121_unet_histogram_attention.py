"""
An implementation combining dense121, unet and residual dense block. Reference image added
"""
from collections import OrderedDict

import torch
import torch.nn as nn
import torch.nn.functional as F
import sys
import cv2

from torchvision import models as torch_models
from torchvision.models import resnet34
from torchvision.models.resnet import BasicBlock, Bottleneck

from models.networks import _DenseBlock, _Transition, RDB, GaussianHistogram, AttentionExtractModule
from utils import helper

class ResidualBlock(nn.Module):
    def __init__(self, in_channels, out_channels, kernel_size=3, padding=1, stride=1):
        super(ResidualBlock, self).__init__()
        self.padding1 = nn.ReflectionPad2d(padding)
        self.conv1 = nn.Conv2d(in_channels, out_channels, kernel_size=kernel_size, padding=0, stride=stride)
        self.bn1 = nn.InstanceNorm2d(out_channels)
        self.prelu = nn.PReLU()
        self.padding2 = nn.ReflectionPad2d(padding)
        self.conv2 = nn.Conv2d(in_channels, out_channels, kernel_size=kernel_size, padding=0, stride=stride)
        self.bn2 = nn.InstanceNorm2d(out_channels)

    def forward(self, x):
        residual = x
        out = self.padding1(x)
        out = self.conv1(out)
        out = self.bn1(out)
        out = self.prelu(out)
        out = self.padding2(out)
        out = self.conv2(out)
        out = self.bn2(out)
        out += residual
        out = self.prelu(out)
        return out

class WarpNet(nn.Module):
    """
    Inputs are the res34 features
    """
    def __init__(self, feat1=64, feat2=128, feat3=256, feat4=512):
        super(WarpNet, self).__init__()
        self.feature_channel = 64
        self.in_channels = self.feature_channel * 4
        self.inter_channels = 256
        # 44*44
        self.layer2_1 = nn.Sequential(
            nn.ReflectionPad2d(1),
            nn.Conv2d(feat1, 128, kernel_size=3, padding=0, stride=1),
            nn.InstanceNorm2d(128),
            nn.PReLU(),
            nn.ReflectionPad2d(1),
            nn.Conv2d(128, self.feature_channel, kernel_size=3, padding=0, stride=2),
            nn.InstanceNorm2d(self.feature_channel),
            nn.PReLU(),
        )

        self.layer3_1 = nn.Sequential(
            nn.ReflectionPad2d(1),
            nn.Conv2d(feat2, 128, kernel_size=3, padding=0, stride=1),
            nn.InstanceNorm2d(128),
            nn.PReLU(),
            nn.ReflectionPad2d(1),
            nn.Conv2d(128, self.feature_channel, kernel_size=3, padding=0, stride=1),
            nn.InstanceNorm2d(self.feature_channel),
            nn.PReLU(),
        )

        # 22*22->44*44
        self.layer4_1 = nn.Sequential(
            nn.ReflectionPad2d(1),
            nn.Conv2d(feat3, 256, kernel_size=3, padding=0, stride=1),
            nn.InstanceNorm2d(256),
            nn.PReLU(),
            nn.ReflectionPad2d(1),
            nn.Conv2d(256, self.feature_channel, kernel_size=3, padding=0, stride=1),
            nn.InstanceNorm2d(self.feature_channel),
            nn.PReLU(),
            nn.Upsample(scale_factor=2),
        )

        # 11*11->44*44
        self.layer5_1 = nn.Sequential(
            nn.ReflectionPad2d(1),
            nn.Conv2d(feat4, 256, kernel_size=3, padding=0, stride=1),
            nn.InstanceNorm2d(256),
            nn.PReLU(),
            nn.Upsample(scale_factor=2),
            nn.ReflectionPad2d(1),
            nn.Conv2d(256, self.feature_channel, kernel_size=3, padding=0, stride=1),
            nn.InstanceNorm2d(self.feature_channel),
            nn.PReLU(),
            nn.Upsample(scale_factor=2),
        )

        self.layer = nn.Sequential(
            ResidualBlock(self.feature_channel * 4, self.feature_channel * 4, kernel_size=3, padding=1, stride=1),
            ResidualBlock(self.feature_channel * 4, self.feature_channel * 4, kernel_size=3, padding=1, stride=1),
            ResidualBlock(self.feature_channel * 4, self.feature_channel * 4, kernel_size=3, padding=1, stride=1),
        )

        self.theta = nn.Conv2d(
            in_channels=self.in_channels, out_channels=self.inter_channels, kernel_size=1, stride=1, padding=0
        )
        self.phi = nn.Conv2d(
            in_channels=self.in_channels, out_channels=self.inter_channels, kernel_size=1, stride=1, padding=0
        )

        self.upsampling = nn.Upsample(scale_factor=4)

    def forward(
        self,
        B_hist,
        A_relu2_1,
        A_relu3_1,
        A_relu4_1,
        A_relu5_1,
        B_relu2_1,
        B_relu3_1,
        B_relu4_1,
        B_relu5_1,
        temperature=0.001 * 5,
        detach_flag=False,
    ):
        batch_size = B_hist.shape[0]

        # scale feature size to 44*44
        A_feature2_1 = self.layer2_1(A_relu2_1)
        B_feature2_1 = self.layer2_1(B_relu2_1)
        A_feature3_1 = self.layer3_1(A_relu3_1)
        B_feature3_1 = self.layer3_1(B_relu3_1)
        A_feature4_1 = self.layer4_1(A_relu4_1)
        B_feature4_1 = self.layer4_1(B_relu4_1)
        A_feature5_1 = self.layer5_1(A_relu5_1)
        B_feature5_1 = self.layer5_1(B_relu5_1)

        print("\nFEATURE SCALES TO 44*44:")
        print("\tARELU2_1- " + str(A_relu2_1.shape))
        print("\tAFEATURE2_1- " + str(A_feature2_1.shape))
        print("\tARELU3_1- " + str(A_relu3_1.shape))
        print("\tAFEATURE3_1- " + str(A_feature3_1.shape))
        print("\tARELU4_1- " + str(A_relu4_1.shape))
        print("\tAFEATURE4_1- " + str(A_feature4_1.shape))
        print("\tARELU5_1- " + str(A_relu5_1.shape))
        print("\tAFEATURE5_1- " + str(A_feature5_1.shape))

        print("\n")

        # concatenate features
        if A_feature5_1.shape[2] != A_feature2_1.shape[2] or A_feature5_1.shape[3] != A_feature2_1.shape[3]:
            A_feature2_1 = padding_customize(A_feature2_1, A_feature5_1)
            A_feature3_1 = padding_customize(A_feature3_1, A_feature5_1)
            A_feature4_1 = padding_customize(A_feature4_1, A_feature5_1)

        if B_feature5_1.shape[2] != B_feature2_1.shape[2] or B_feature5_1.shape[3] != B_feature2_1.shape[3]:
            B_feature2_1 = padding_customize(B_feature2_1, B_feature5_1)
            B_feature3_1 = padding_customize(B_feature3_1, B_feature5_1)
            B_feature4_1 = padding_customize(B_feature4_1, B_feature5_1)

        A_features = self.layer(torch.cat((A_feature2_1, A_feature3_1, A_feature4_1, A_feature5_1), 1))
        B_features = self.layer(torch.cat((B_feature2_1, B_feature3_1, B_feature4_1, B_feature5_1), 1))

        print("A Features:")
        print("\t-" + str(A_features.shape))
        print("B Features:")
        print("\t-" + str(B_features.shape))
        print ("\n")
        
        # pairwise cosine similarity
        print("-- PAIRWISE COSINE SIMILARITY --")
        theta = self.theta(A_features).view(batch_size, self.inter_channels, -1)  # 2*256*(feature_height*feature_width)
        print("THETA VIEW = " + str(theta.shape))
        theta = theta - theta.mean(dim=-1, keepdim=True)  # center the feature
        print("THETA MEAN = " + str(theta.shape))
        theta_norm = torch.norm(theta, 2, 1, keepdim=True) + sys.float_info.epsilon
        print("THETA NORM = " + str(theta_norm.shape))
        theta = torch.div(theta, theta_norm)
        print("THETA DIV = " + str(theta.shape))
        theta_permute = theta.permute(0, 2, 1)  # 2*(feature_height*feature_width)*256
        print("THETA PERMUTE = " + str(theta_permute.shape))
        phi = self.phi(B_features).view(batch_size, self.inter_channels, -1)  # 2*256*(feature_height*feature_width)
        print("PHI VIEW = " + str(phi.shape))
        phi = phi - phi.mean(dim=-1, keepdim=True)  # center the feature
        print("PHI MEAN = " + str(phi.shape))
        phi_norm = torch.norm(phi, 2, 1, keepdim=True) + sys.float_info.epsilon
        print("PHI NORM = " + str(phi_norm.shape))
        phi = torch.div(phi, phi_norm)
        print("PHI DIV = " + str (phi.shape))
        f = torch.matmul(theta_permute, phi)  # 2*(feature_height*feature_width)*(feature_height*feature_width)
        print("f = " + str(f.shape))
        
        if detach_flag:
            f = f.detach()

        print("\n")
        f_similarity = f.unsqueeze_(dim=1)
        print("f_similarity UNSQUEEZE= " + str(f.shape))
        similarity_map = torch.max(f_similarity, -1, keepdim=True)[0]
        print("similarity_map MAX = " + str(similarity_map.shape))
        similarity_map = similarity_map.view(batch_size, 1, A_feature2_1.shape[2],  A_feature2_1.shape[3])
        print("similarity_map VIEW = " + str(similarity_map.shape))
        print("\n")
        # f can be negative
        f_WTA = f
        
        f_WTA = f_WTA / temperature
        print("f_WTA / TEMP = " + str(f_WTA.shape))
        print("f_WTA SQUEEZE = " + str(f_WTA.squeeze_().shape))
        f_div_C = F.softmax(f_WTA.squeeze_(), dim=-1)  # 2*1936*1936;
        print("f_div_C = " + str(f_div_C.shape))
        # downsample the reference histogram
        feature_height, feature_width = B_hist.shape[2], B_hist.shape[3]
        B_hist = B_hist.view(batch_size, 512, -1)
        print("BHIST VIEW: " + str(B_hist.shape))
        B_hist = B_hist.permute(0, 2, 1)
        print("BHIST PERMUTE: " + str(B_hist.shape))
        y_hist = torch.matmul(f_div_C, B_hist)
        print("Y_HIST MATMUL: " + str(y_hist.shape))
        y_hist = y_hist.permute(0, 2, 1).contiguous()
        print("Y_HIST PERMUTE: " + str(y_hist.shape))
        y_hist_1 = y_hist.view(batch_size, 512, feature_height, feature_width)
        
        # upsample, downspale the wrapped histogram feature for multi-level fusion
        upsample = nn.Upsample(scale_factor=2)
        y_hist_0 = upsample(y_hist_1)
        y_hist_2 = F.avg_pool2d(y_hist_1, 2)
        y_hist_3 = F.avg_pool2d(y_hist_1, 4)

        print("\n")
        print("Y_HIST_0: " + str(y_hist_0.shape))
        print("Y_HIST_1: " + str(y_hist_1.shape))
        print("Y_HIST_2: " + str(y_hist_2.shape))
        print("Y_HIST_3: " + str(y_hist_3.shape))
        print("\n")

        # do the same thing to similarity map
        similarity_map_0 = upsample(similarity_map)
        similarity_map_1 = similarity_map
        similarity_map_2 = F.avg_pool2d(similarity_map_1, 2)
        similarity_map_3 = F.avg_pool2d(similarity_map_1, 4)
        print("Similarity map 0: " + str(similarity_map_0.shape))
        print("Similarity map 1: " + str(similarity_map_1.shape))
        print("Similarity map 2: " + str(similarity_map_2.shape))
        print("Similarity map 3: " + str(similarity_map_3.shape))
        print("\n")

        return [(y_hist_0, similarity_map_0), (y_hist_1, similarity_map_1),
                (y_hist_2, similarity_map_2), (y_hist_3, similarity_map_3)]

class HistogramLayerLocal(nn.Module):
    def __init__(self):
        super().__init__()
        self.hist_layer = GaussianHistogram(bins=256, min=-1., max=1., sigma=0.01, require_grad=False)

    def forward(self, x, ref, attention_mask=None):
        channels = ref.shape[1]
        #print("\n")
        #print("NUMBER CHANNELS = " + str(channels))
        #print("LEN X SHAPE = " + str(len(x.shape)))
        if len(x.shape) == 3:
            ref = F.interpolate(ref,
                                size=(x.shape[1], x.shape[2]),
                                mode='bicubic')
            if not type(attention_mask) == type(None):
                attention_mask = torch.unsqueeze(attention_mask, 1)
                attention_mask = F.interpolate(attention_mask,
                                               size=(x.shape[1], x.shape[2]),
                                               mode='bicubic')
        else:
            ref = F.interpolate(ref,
                                size=(x.shape[2], x.shape[3]),
                                mode='bicubic')
            if not type(attention_mask) == type(None):
                attention_mask = torch.unsqueeze(attention_mask, 1)
                attention_mask = F.interpolate(attention_mask,
                                               size=(x.shape[2], x.shape[3]),
                                               mode='bicubic')
                attention_mask = torch.flatten(attention_mask, start_dim=1, end_dim=-1)
        #print("Ref after interpolation:")
        #print("\t-" + str(ref.shape))
        #print("\n")
        layers = []
        for i in range(channels):
            input_channel = torch.flatten(ref[:, i, :, :], start_dim=1, end_dim=-1)
            #print("Shape after flatten = " + str(input_channel.shape))
            input_hist, hist_dist = self.hist_layer(input_channel, attention_mask)
            #print("HIST DIST SHAPE = " + str(hist_dist.shape))
            hist_dist = hist_dist.view(-1, 256, ref.shape[2], ref.shape[3])
            #print("HIST DIST SHAPE VIEW = " + str(hist_dist.shape))
            layers.append(hist_dist)
        final_layers = torch.cat(layers, 1)
        #print("Final LAYERS SHAPE = " + str(final_layers.shape))
        return final_layers


class DoubleConv(nn.Module):
    """
    Double convoltuion
    Args:
        in_channels: input channel num
        out_channels: output channel num
    """

    def __init__(self, in_channels, out_channels):
        super().__init__()
        self.double_conv = nn.Sequential(
            nn.Conv2d(in_channels, out_channels, kernel_size=3, padding=1),
            nn.ReLU(inplace=False),
            nn.Conv2d(out_channels, out_channels, kernel_size=3, padding=1),
            nn.ReLU(inplace=False)
        )

    def forward(self, x):
        return self.double_conv(x)


class HistFusionModule(nn.Module):
    """
    Global pooling fused with histogram
    """

    def __init__(self, in_features, out_features):
        super().__init__()
        self.conv = nn.Conv2d(in_features, out_features, kernel_size=3, padding=1)
        self.RDB = RDB(out_features, 4, 32)

    def forward(self, feature):
        print("\n-- FUSION MODULE --")
        feature = self.conv(feature)
        print("FEATUE CONV")
        feature = self.RDB(feature)

        return feature


class Up(nn.Module):
    """Upscaling then double conv"""

    def __init__(self, current_channels, prev_channels, out_channels,
                 bilinear=True, nDenseLayer=3, growthRate=32, global_pool=False):
        super().__init__()
        self.global_pool = global_pool
        # if bilinear, use the normal convolutions to reduce the number of channels
        if bilinear:
            self.up = nn.Upsample(scale_factor=2, mode='bilinear', align_corners=True)

        else:
            self.up = nn.ConvTranspose2d(current_channels, current_channels, kernel_size=2, stride=2)

        self.RDB = RDB(current_channels, nDenseLayer, growthRate)
        self.conv = DoubleConv(current_channels + prev_channels, out_channels)

    def forward(self, x1, x2):
        
        h, w = x2.shape[2], x2.shape[3]
        if not self.global_pool:
            x1 = self.up(x1)
            # input is CHW
            diffY = torch.tensor([x2.size()[2] - x1.size()[2]])
            diffX = torch.tensor([x2.size()[3] - x1.size()[3]])
            # in case input size are odd
            x1 = F.pad(x1, [diffX // 2, diffX - diffX // 2,
                            diffY // 2, diffY - diffY // 2])
        else:
            x1 = F.upsample(x1, size=(h, w), mode='bilinear')

        x1 = self.RDB(x1)
        print("\n x1 SHAPE in up: " + str(x1.shape))
        print("\n x2 SHAPE in up: " + str(x2.shape))
        x = torch.cat([x2, x1], dim=1)
        return self.conv(x)


class Dense121UnetHistogramAttention(nn.Module):
    """
    A combination of Dense121, Unet and residual dense block. The style is a little wired since
    we need to comply the same name rule as the torch.hub.densenet121 to retrieve pretrained model
    Args:
        args: some optional params
        growth_rate: don't change it since we are using pretrained weights
        block_config: don't change it since we are using pretrained weights
        num_init_features: don't change it since we are using pretrained weights
        bn_size: don't change it since we are using pretrained weights
    """

    def __init__(self, args, color_pretrain=False, growth_rate=32, block_config=(6, 12, 24, 48),
                 num_init_features=64, bn_size=4):
        super(Dense121UnetHistogramAttention, self).__init__()
        self.color_pretrain = color_pretrain
        
        # reference local histogram layer
        self.hist_layer_local = HistogramLayerLocal()

        # First convolution
        self.features = nn.Sequential(OrderedDict([
            ('conv0_0', nn.Conv2d(1, num_init_features, kernel_size=7, stride=1,
                                  padding=3, bias=False)),
            ('relu0', nn.ReLU(inplace=True)),
            ('pool0', nn.MaxPool2d(kernel_size=3, stride=2, padding=1)),
        ]))

        # Encoder part
        num_features = num_init_features
        for i, num_layers in enumerate(block_config):
            block = _DenseBlock(
                num_layers=num_layers,
                num_input_features=num_features,
                bn_size=bn_size,
                growth_rate=growth_rate,
                drop_rate=args['drop_rate']
            )
            self.features.add_module('denseblock%d' % (i + 1), block)
            num_features = num_features + num_layers * growth_rate

            # downsampling
            trans = _Transition(num_input_features=num_features,
                                num_output_features=num_features // 2)
            self.features.add_module('transition%d' % (i + 1), trans)
            num_features = num_features // 2

        # histogram distribution fusion part, feature + similarity mask + histogram
        self.hf_1 = HistFusionModule(128 + 1 + 256 * 2, 128)
        self.hf_2 = HistFusionModule(256 + 1 + 256 * 2, 256)
        self.hf_3 = HistFusionModule(512 + 1 + 256 * 2, 512)
        self.hf_4 = HistFusionModule(1024 + 1 + 256 * 2, 1024)

        # Decoder Part
        self.up0 = Up(1024, 2048, 1024, args['bilinear'], args['nDenseLayer'][0], args['growthRate'])
        self.up1 = Up(1024, 1024, 512, args['bilinear'], args['nDenseLayer'][0], args['growthRate'])
        self.up2 = Up(512, 512, 256, args['bilinear'], args['nDenseLayer'][1], args['growthRate'])
        self.up3 = Up(256, 256, 128, args['bilinear'], args['nDenseLayer'][2], args['growthRate'])
        self.up4 = Up(128, 64, 64, args['bilinear'], args['nDenseLayer'][3], args['growthRate'])

        nChannels = args['input_channel']
        self.conv_final = nn.Conv2d(64, nChannels, kernel_size=3, padding=1, bias=True)
        self.warp_net = WarpNet()

    def load_pretrained(self):
        pretrained_model = torch.hub.load('pytorch/vision:v0.4.0', 'densenet121', pretrained=True)
        pretrained_dict = pretrained_model.state_dict()
        model_dict = self.state_dict()

        # 1. filter out unnecessary keys
        pretrained_dict = {k: v for k, v in pretrained_dict.items() if k in model_dict}
        # 2. overwrite entries in the existing state dict
        model_dict.update(pretrained_dict)
        # 3. load the new state dict
        self.load_state_dict(model_dict)

    def normalize_data(self, x):
        """
        Normalize the data for attention module
        """
        device = x.device

        mean_r = torch.ones(1, x.shape[2], x.shape[3]) * 0.485
        mean_g = torch.ones(1, x.shape[2], x.shape[3]) * 0.456
        mean_b = torch.ones(1, x.shape[2], x.shape[3]) * 0.406
        mean = torch.cat((mean_r, mean_g, mean_b), dim=0)
        mean = mean.to(device)

        std_r = torch.ones(1, x.shape[2], x.shape[3]) * 0.229
        std_g = torch.ones(1, x.shape[2], x.shape[3]) * 0.224
        std_b = torch.ones(1, x.shape[2], x.shape[3]) * 0.225
        std = torch.cat((std_r, std_g, std_b), dim=0)
        std = std.to(device)

        normalized_data = (x - mean) / std
        return normalized_data

    def forward(self, x, x_gray, ref, ref_gray, att_model):
        """
        :param x: input data
        :param gt: gt_data
        :param gt: gt_gray
        :param att_model: pretrained resent34
        """
        # Input size is 256x256
        print("Input SHAPE: ")
        print("\t-" + str(x.shape))
        print("Ref SHAPE:")
        print("\t-" + str(ref.shape))
        print("\n")

        # shallow conv
        feature0 = self.features.relu0(self.features.conv0_0(x))
        print("feature0 after first conv ")
        print("\t-" + str(feature0.shape))
        down0 = self.features.pool0(feature0)
        print("down0 after pool")
        print("\t-" + str(down0.shape))

        # normalize data for attention mask
        normalized_ref = self.normalize_data(ref_gray.repeat(1, 3, 1, 1))
        normalized_x = self.normalize_data(x_gray.repeat(1, 3, 1, 1))
        
        print("\nNormalized SHAPES:")
        print(normalized_ref.shape)
        print(normalized_x.shape)

        # attention mask for both input and ground truth(size divide 4, 8, 16, 32)
        ref_attention_masks, ref_res_features = att_model(normalized_ref)
        x_attention_masks, x_res_features = att_model(normalized_x)

        # generate histogram for different size
        ref_resize_by_8 = F.avg_pool2d(ref, 8)
        print("\Resized SHAPE:")
        print(ref_resize_by_8.shape)
        x_resize_by_8 = F.avg_pool2d(x, 8)
        ref_hist = self.hist_layer_local(x_resize_by_8, ref_resize_by_8)

        # generate the similarity map and wrapped features
        sim_feature = self.warp_net(ref_hist,
                                    x_res_features[0], x_res_features[1], x_res_features[2], x_res_features[3],
                                    ref_res_features[0], ref_res_features[1], ref_res_features[2], ref_res_features[3])

        # dense block 1
        feature1 = self.features.denseblock1(down0)
        print("feature1 SHAPE AFTER DENSEBLOCK: ")
        print("\t-" + str(feature1.shape))
        down1 = self.features.transition1(feature1)
        print("down1 SHAPE AFTER TRANSITION: ")
        print("\t-" + str(down1.shape))
        down1 = torch.cat([down1, sim_feature[0][1], sim_feature[0][0]], 1)
        print("down1 SHAPE AFTER CONCAT: ")
        print("\t-" + str(down1.shape))
        down1 = self.hf_1(down1)
        print("down1 SHAPE AFTER HF1: ")
        print("\t-" + str(down1.shape))

        # dense block 2
        feature2 = self.features.denseblock2(down1)
        down2 = self.features.transition2(feature2)
        down2 = torch.cat([down2, sim_feature[1][1], sim_feature[1][0]], 1)
        down2 = self.hf_2(down2)
        print("down2 SHAPE AFTER HF2: ")
        print("\t-" + str(down2.shape))
        # dense block3
        feature3 = self.features.denseblock3(down2)
        down3 = self.features.transition3(feature3)
        down3 = torch.cat([down3, sim_feature[2][1], sim_feature[2][0]], 1)
        down3 = self.hf_3(down3)
        print("down3 SHAPE AFTER HF3: ")
        print("\t-" + str(down3.shape))
        # dense block 4
        feature4 = self.features.denseblock4(down3)
        down4 = self.features.transition4(feature4)
        down4 = torch.cat([down4, sim_feature[3][1], sim_feature[3][0]], 1)
        down4 = self.hf_4(down4)
        print("down4 SHAPE AFTER HF4: ")
        print("\t-" + str(down4.shape))

        # up
        up = self.up0(down4, feature4)
        print("up0 SHAPE:")
        print("\t-" + str(up.shape))

        up = self.up1(up, feature3)
        print("up1 SHAPE:")
        print("\t-" + str(up.shape))

        up = self.up2(up, feature2)
        print("up2 SHAPE:")
        print("\t-" + str(up.shape))

        up = self.up3(up, feature1)
        print("up3 SHAPE:")
        print("\t-" + str(up.shape))
        
        up = self.up4(up, feature0)
        print("up4 SHAPE:")
        print("\t-" + str(up.shape))

        output = self.conv_final(up)
        print("output SHAPE:")
        print("\t-" + str(output.shape))
        results = {'output': output}
        return results


if __name__ == '__main__':
    # unit test
    data = torch.randn((2, 1, 256, 256))
    gt = torch.randn((2, 3, 256, 256))
    gt_gray = torch.randn((2, 1, 256, 256))

    data = data.cuda()
    gt = gt.cuda()
    gt_gray = gt_gray.cuda()

    base_resnet34 = resnet34(pretrained=True)
    att_model = AttentionExtractModule(BasicBlock, [3, 4, 6, 3])
    att_model.load_state_dict(base_resnet34.state_dict())
    att_model.cuda()
    att_model.eval()

    args = {'input_channel': 3, 'growthRate': 32, 'bilinear': True, 'drop_rate': 0.5,
            'nDenseLayer': [8, 12, 6, 4], 'pretrained': True}
    model = Dense121UnetHistogramAttention(args)
    model.cuda()
    output = model(data, gt, gt_gray, att_model)
