"""
Base on codes from rwightman:
https://github.com/rwightman/pytorch-image-models/blob/master/timm/models/vision_transformer.py
"""
from functools import partial
from collections import OrderedDict
import torch
import torch.nn as nn
from utils import MyAt
    
    
def drop_path(x, drop_prob: float = 0., training: bool = False):
    if drop_prob == 0. or not training:
        return x
    keep_prob = 1 - drop_prob
    shape = (x.shape[0],) + (1,) * (x.ndim - 1)  # (shape[0],1,1,1)
    random_tensor = keep_prob + torch.rand(shape, dtype=x.dtype, device=x.device)   # rand范围在[0~1]之间, +keep_prob在[keep_prob~keep_prob+1]之间
    random_tensor.floor_()  # 只保留0或者1
    output = x.div(keep_prob) * random_tensor   # x.div(keep_prob)个人理解是为了强化保留部分的x
    return output


class DropPath(nn.Module):
    def __init__(self, drop_prob=None):
        super(DropPath, self).__init__()
        self.drop_prob = drop_prob

    def forward(self, x):
        return drop_path(x, self.drop_prob, self.training)


class PatchEmbed(nn.Module):
    def __init__(self, img_size=224, patch_size=16, in_c=3, embed_dim=768, norm_layer=None):
        super().__init__()
        img_size = (img_size, img_size)
        patch_size = (patch_size, patch_size)
        self.img_size = img_size
        self.patch_size = patch_size
        self.grid_size = (img_size[0] // patch_size[0], img_size[1] // patch_size[1])
        self.num_patches = self.grid_size[0] * self.grid_size[1]
        self.proj = nn.Conv2d(in_c, embed_dim, kernel_size=patch_size, stride=patch_size)
        self.norm = norm_layer(embed_dim) if norm_layer else nn.Identity()

    def forward(self, x):
        B, C, H, W = x.shape
        assert H == self.img_size[0] and W == self.img_size[1], \
            f"Input image size ({H}*{W}) doesn't match model ({self.img_size[0]}*{self.img_size[1]})."
        # flatten: [B, C, H, W] -> [B, C, HW]
        # transpose: [B, C, HW] -> [B, HW, C]
        # 一维展平
        x = self.proj(x).flatten(2).transpose(1, 2)
        x = self.norm(x)
        return x

class PositionEmbs(nn.Module):
    def __init__(self, num_patches, emb_dim, dropout_rate=0.1):
        super(PositionEmbs, self).__init__()
        self.pos_embedding = nn.Parameter(torch.zeros(1, num_patches + 1, emb_dim))
        if dropout_rate > 0:
            self.dropout = nn.Dropout(dropout_rate)
        else:
            self.dropout = None
        nn.init.trunc_normal_(self.pos_embedding, std=0.02)
    def forward(self, x):
        out = x + self.pos_embedding
        if self.dropout:
            out = self.dropout(out)
        return out


class Mlp(nn.Module):
    def __init__(self, in_features, hidden_features=None, out_features=None, act_layer=nn.GELU, drop=0.):
        super().__init__()
        out_features = out_features or in_features
        hidden_features = hidden_features or in_features
        self.fc1 = nn.Linear(in_features, hidden_features)
        self.act = act_layer()
        self.fc2 = nn.Linear(hidden_features, out_features)
        self.drop = nn.Dropout(drop)

    def forward(self, x):
        x = self.fc1(x)
        x = self.act(x)
        x = self.drop(x)
        x = self.fc2(x)
        x = self.drop(x)
        return x

class Attention(nn.Module):
    def __init__(self,
                 dim,
                 num_heads=8,
                 qkv_bias=False,
                 qk_scale=None,
                 attn_drop_ratio=0.,
                 proj_drop_ratio=0.):
        super(Attention, self).__init__()
        self.num_heads = num_heads
        head_dim = dim // num_heads
        self.scale = qk_scale or head_dim ** -0.5
        self.qkv = nn.Linear(dim, dim * 3, bias=qkv_bias)
        self.attn_drop = nn.Dropout(attn_drop_ratio)
        self.proj = nn.Linear(dim, dim)
        self.proj_drop = nn.Dropout(proj_drop_ratio)
        
        self.at1 = MyAt()
        self.at2 = MyAt()
        self.softmax = nn.Softmax(dim=-1)

    def forward(self, x):
        B, N, C = x.shape
        qkv = self.qkv(x).reshape(B, N, 3, self.num_heads, C // self.num_heads).permute(2, 0, 3, 1, 4)
        q, k, v = qkv[0], qkv[1], qkv[2]
        attn = self.at1(q, k.transpose(-2, -1))
        attn *= self.scale
        attn = self.softmax(attn)
        attn = self.attn_drop(attn)
        
        x = self.at2(attn, v)
        x = x.transpose(1, 2).reshape(B, N, -1)
        
        x = self.proj(x)
        x = self.proj_drop(x)
        return x



class Block(nn.Module):
    def __init__(self,
                 dim,
                 num_heads,
                 mlp_ratio=4.,
                 qkv_bias=False,
                 qk_scale=None,
                 drop_ratio=0.,
                 attn_drop_ratio=0.,
                 drop_path_ratio=0.,
                 act_layer=nn.GELU,
                 norm_layer=nn.LayerNorm):
        super(Block, self).__init__()
        self.norm1 = norm_layer(dim)
        self.attn = Attention(dim, num_heads=num_heads, qkv_bias=qkv_bias, qk_scale=qk_scale,
                              attn_drop_ratio=attn_drop_ratio, proj_drop_ratio=drop_ratio)
        self.drop_path = DropPath(drop_path_ratio) if drop_path_ratio > 0. else nn.Identity()
        self.norm2 = norm_layer(dim)
        mlp_hidden_dim = int(dim * mlp_ratio)
        self.mlp = Mlp(in_features=dim, hidden_features=mlp_hidden_dim, act_layer=act_layer, drop=drop_ratio)

    def forward(self, x):
        x = x + self.drop_path(self.attn(self.norm1(x)))
        x = x + self.drop_path(self.mlp(self.norm2(x)))
        return x


class VisionTransformer(nn.Module):
    def __init__(self, img_size=224, patch_size=16, in_c=3, num_classes=1000,
                 embed_dim=768, depth=12, num_heads=12, mlp_ratio=4.0, qkv_bias=True,
                 qk_scale=None, representation_size=None, distilled=False, drop_ratio=0.,
                 attn_drop_ratio=0., drop_path_ratio=0., embed_layer=PatchEmbed, norm_layer=None,
                 act_layer=None):
        """
        Args:
            img_size (int, tuple): input image size
            patch_size (int, tuple): patch size
            in_c (int): number of input channels
            num_classes (int): number of classes for classification head
            embed_dim (int): embedding dimension
            depth (int): depth of transformer
            num_heads (int): number of attention heads
            mlp_ratio (int): ratio of mlp hidden dim to embedding dim
            qkv_bias (bool): enable bias for qkv if True
            qk_scale (float): override default qk scale of head_dim ** -0.5 if set
            representation_size (Optional[int]): enable and set representation layer (pre-logits) to this value if set
            distilled (bool): model includes a distillation token and head as in DeiT models
            drop_ratio (float): dropout rate
            attn_drop_ratio (float): attention dropout rate
            drop_path_ratio (float): stochastic depth rate
            embed_layer (nn.Module): patch embedding layer
            norm_layer: (nn.Module): normalization layer
        """
        super(VisionTransformer, self).__init__()
        self.num_classes = num_classes
        # embed_dim默认tansformer的base 768
        self.num_features = self.embed_dim = embed_dim  # num_features for consistency with other models
        # 源码distilled是为了其他任务,分类暂时不考虑
        self.num_tokens = 2 if distilled else 1
        # LayerNorm:对每单个batch进行的归一化
        norm_layer = norm_layer or partial(nn.LayerNorm, eps=1e-6)
        # act_layer默认tansformer的GELU
        act_layer = act_layer or nn.GELU
        # embed_layer默认是patch embedding,在其他应用中应该会有其他选择
        # patch embedding过程
        self.patch_embed = embed_layer(img_size=img_size, patch_size=patch_size, in_c=in_c, embed_dim=embed_dim)
        # patche个数
        num_patches = self.patch_embed.num_patches
        # class token
        self.cls_token = nn.Parameter(torch.zeros(1, 1, embed_dim))
        self.dist_token = nn.Parameter(torch.zeros(1, 1, embed_dim)) if distilled else None
        # positional embedding过程
        # self.pos_embedding = PositionEmbs(num_patches, embed_dim, drop_ratio)

        self.pos_embed = nn.Parameter(torch.zeros(1, num_patches + self.num_tokens, embed_dim))
        self.pos_drop = nn.Dropout(p=drop_ratio)

        # depth是Block的个数
        # 不同block层数 drop_ratio的概率不同,越深度越高
        dpr = [x.item() for x in torch.linspace(0, drop_path_ratio, depth)]  # stochastic depth decay rule
        # blocks搭建
        self.blocks = nn.Sequential(*[
            Block(dim=embed_dim, num_heads=num_heads, mlp_ratio=mlp_ratio, qkv_bias=qkv_bias, qk_scale=qk_scale,
                  drop_ratio=drop_ratio, attn_drop_ratio=attn_drop_ratio, drop_path_ratio=dpr[i],
                  norm_layer=norm_layer, act_layer=act_layer)
            for i in range(depth)
        ])

        self.norm = norm_layer(embed_dim)
        # Representation layer
        if representation_size and not distilled:
            self.has_logits = True
            self.num_features = representation_size
            self.pre_logits = nn.Sequential(OrderedDict([
                ("fc", nn.Linear(embed_dim, representation_size)),
                ("act", nn.Tanh())
            ]))
        else:
            self.has_logits = False
            self.pre_logits = nn.Identity()

        # Classifier head(s)
        self.head = nn.Linear(self.num_features, num_classes) if num_classes > 0 else nn.Identity()
        self.head_dist = None
        if distilled:
            self.head_dist = nn.Linear(self.embed_dim, self.num_classes) if num_classes > 0 else nn.Identity()

        # Weight init
        if self.dist_token is not None:
            nn.init.trunc_normal_(self.dist_token, std=0.02)

        nn.init.trunc_normal_(self.cls_token, std=0.02)
        self.apply(_init_vit_weights)

    def forward_features(self, x):
        # [B, C, H, W] -> [B, num_patches, embed_dim]
        x = self.patch_embed(x)  # [B, 196, 768]
        cls_token = self.cls_token.expand(x.shape[0], -1, -1)
        # self.dist_token暂时可以忽略
        if self.dist_token is None:
            x = torch.cat((cls_token, x), dim=1)  # [B, 197, 768]
        else:
            x = torch.cat((cls_token, self.dist_token.expand(x.shape[0], -1, -1), x), dim=1)
        # x = self.pos_embedding(x)
        x = self.pos_drop(x + self.pos_embed)
        x = self.blocks(x)
        x = self.norm(x)
        if self.dist_token is None:
            return self.pre_logits(x[:, 0])
        else:
            return x[:, 0], x[:, 1]

    def forward(self, x,T=1):
        
        x1 = torch.clone(x)
        x1 = self.forward_features(x1)
        if self.head_dist is not None:
            x1, x_dist = self.head(x1[0]), self.head_dist(x1[1])
            if self.training and not torch.jit.is_scripting():
                return x1, x_dist
            else:
                return (x1 + x_dist) / 2
        else:
            x1 = self.head(x1)
        return x1


def _init_vit_weights(m):
    """
    ViT weight initialization
    :param m: module
    """
    if isinstance(m, nn.Linear):
        nn.init.trunc_normal_(m.weight, std=.01)
        if m.bias is not None:
            nn.init.zeros_(m.bias)
    elif isinstance(m, nn.Conv2d):
        nn.init.kaiming_normal_(m.weight, mode="fan_out")
        if m.bias is not None:
            nn.init.zeros_(m.bias)
    elif isinstance(m, nn.LayerNorm):
        nn.init.zeros_(m.bias)
        nn.init.ones_(m.weight)

        

def vit_tiny_patch16_224(pretrained = False,num_classes: int = 1000,**keywords):
    model = VisionTransformer(img_size=224,
                              patch_size=16,
                              embed_dim=192,
                              depth=12,
                              num_heads=3,
                              representation_size=None,
                              num_classes=num_classes)
    return model

def vit_tiny_patch16_384(pretrained = False,num_classes: int = 1000,**keywords):
    model = VisionTransformer(img_size=384,
                              patch_size=16,
                              embed_dim=192,
                              depth=12,
                              num_heads=3,
                              representation_size=None,
                              num_classes=num_classes)
    return model

def vit_small_patch32_224(pretrained = False,num_classes: int = 1000,**keywords):
    model = VisionTransformer(img_size=224,
                              patch_size=32,
                              embed_dim=384,
                              depth=12,
                              num_heads=6,
                              representation_size=None,
                              num_classes=num_classes)
    return model


def vit_small_patch32_384(pretrained = False,num_classes: int = 1000,**keywords):
    model = VisionTransformer(img_size=384,
                              patch_size=32,
                              embed_dim=384,
                              depth=12,
                              num_heads=6,
                              representation_size=None,
                              num_classes=num_classes)
    return model


def vit_small_patch16_224(pretrained = False,num_classes: int = 1000,**keywords):
    model = VisionTransformer(img_size=224,
                              patch_size=16,
                              embed_dim=384,
                              depth=12,
                              num_heads=6,
                              representation_size=None,
                              num_classes=num_classes)
    return model

def vit_small_patch16_384(pretrained = False,num_classes: int = 1000,**keywords):
    model = VisionTransformer(img_size=384,
                              patch_size=16,
                              embed_dim=384,
                              depth=12,
                              num_heads=6,
                              representation_size=None,
                              num_classes=num_classes)
    return model

def vit_base_patch32_224(pretrained: bool = False, **kwargs) -> VisionTransformer:
    model = VisionTransformer(img_size=224,
                              patch_size=32,
                              embed_dim=768,
                              depth=12,
                              num_heads=12,
                              representation_size=None,
                              num_classes=num_classes)
    return model

def vit_base_patch32_384(pretrained = False,num_classes: int = 1000,**keywords):
    model = VisionTransformer(img_size=384,
                              patch_size=32,
                              embed_dim=768,
                              depth=12,
                              num_heads=12,
                              representation_size=None,
                              num_classes=num_classes)
    return model

def vit_base_patch16_224(pretrained = False,num_classes: int = 1000,**keywords):
    model = VisionTransformer(img_size=224,
                              patch_size=16,
                              embed_dim=768,
                              depth=12,
                              num_heads=12,
                              representation_size=None,
                              num_classes=num_classes)
    return model

def vit_base_patch16_384(pretrained = False,num_classes: int = 1000,**keywords):
    model = VisionTransformer(img_size=384,
                              patch_size=16,
                              embed_dim=768,
                              depth=12,
                              num_heads=12,
                              representation_size=None,
                              num_classes=num_classes)
    return model

def vit_large_patch32_224(pretrained = False,num_classes: int = 1000,**keywords):
    model = VisionTransformer(img_size=224,
                              patch_size=32,
                              embed_dim=1024,
                              depth=24,
                              num_heads=16,
                              representation_size=None,
                              num_classes=num_classes)
    return model

def vit_large_patch32_384(pretrained = False,num_classes: int = 1000,**keywords):
    model = VisionTransformer(img_size=384,
                              patch_size=32,
                              embed_dim=1024,
                              depth=24,
                              num_heads=16,
                              representation_size=None,
                              num_classes=num_classes)
    return model

def vit_large_patch16_224(pretrained = False,num_classes: int = 1000,**keywords):
    model = VisionTransformer(img_size=224,
                              patch_size=16,
                              embed_dim=1024,
                              depth=24,
                              num_heads=16,
                              representation_size=None,
                              num_classes=num_classes)
    return model

def vit_large_patch16_384(pretrained = False,num_classes: int = 1000,**keywords):
    model = VisionTransformer(img_size=384,
                              patch_size=16,
                              embed_dim=1024,
                              depth=24,
                              num_heads=16,
                              representation_size=None,
                              num_classes=num_classes)
    return model
