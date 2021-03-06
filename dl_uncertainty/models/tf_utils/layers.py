# pylint: disable=unexpected-keyword-arg

import inspect

import functools

import numpy as np
import tensorflow as tf

from .variables import conv_weight_variable, bias_variable, vector_variable

# Helper functions (private)


def scoped(fun):

    @functools.wraps(fun)  # preserves the signature (needed for default_arg)
    def wrapper(*args, reuse: bool = None, name: str = None, **kwargs):
        #if reuse == -1:  # TODO: change reuse default to tf.AUTO_REUSE
        #    reuse = tf.get_variable_scope().reuse
        a = list(args) + list(kwargs.values())
        tensors = [v for v in a if type(v) is tf.Tensor]
        with tf.variable_scope(
                name, default_name=fun.__name__, values=tensors, reuse=reuse):
            return fun(*args, **kwargs)

    return wrapper


def default_arg(func, param):
    return inspect.signature(func).parameters[param].default


def default_args(func, params):
    return dict([(p, inspect.signature(func).parameters[p].default)
                 for p in params])


def filter_dict(d: dict, keys):
    if callable(keys):
        return {k: v for k, v in d.items() if keys(k)}
    return {k: v for k, v in d.items() if k in keys}


# Gradient control


def amplify_gradient(x, factor):
    if factor == 1:
        return x
    return factor * x + (1 - factor) * tf.stop_gradient(x)


def amplify_gradient_for_subgraph(subgraph_func, input, factor):
    input = amplify_gradient(input, 1 / factor)
    input = subgraph_func(input)
    return amplify_gradient(input, factor)


# Slicing, joining, copying


def repeat(x, n, axis=0):
    """
    Repeats subarrays in axis. The returned array has shape such that
    shape[i] = x.shape[i] if i != axis, shape[axis] = n*x.shape[axis].
    Based on https://github.com/tensorflow/tensorflow/issues/8246
    :param repeats: list. Numbers of repeats for each dimension    
    """
    repeats = [1] * len(x.shape)
    repeats[axis] = n
    xe = tf.expand_dims(x, -1)
    xt = tf.tile(xe, multiples=[1] + repeats)
    return tf.reshape(xt, tf.shape(x) * repeats)


def repeat_batch(x, n, expand_dims=True):
    multiples = [n] + [1] * (len(x.shape) - 1)
    if expand_dims:  # [N,H,W,C]->[n,N,H,W,C] else: [N,H,W,C]->[nN,H,W,C]
        x = tf.expand_dims(x, 0)
        multiples += [1]
    return tf.tile(x, multiples)


def join_batches(x):  # [n,N,H,W,C]->[nN,H,W,C]
    s = tf.shape(x)
    shape = tf.concat([[s[0] * s[1]], s[2:]], 0)
    return tf.reshape(x, shape)


def split_batch(x, n: int):  # [nN,H,W,C]->[n,N,H,W,C]
    s = tf.shape(x)
    shape = tf.concat([[n, tf.floordiv(s[0], n)], s[1:]], 0)
    return tf.reshape(x, shape)


# Linear, affine


@scoped
def add_bias(x):
    return x + bias_variable(x.shape[-1].value, name='bias')


# Convolution

print_conv = False


@scoped
def conv(x, ksize, width, stride=1, dilation=1, padding='SAME', bias=False):
    """
    A wrapper for tf.nn.conv2d.
    :param x: 4D input "NHWC" tensor 
    :param ksize: int or tuple of 2 ints representing spatial dimensions 
    :param width: number of output channels
    :param stride: int (or float in case of transposed convolution)
    :param dilation: dilation of the kernel
    :param padding: string. "SAME" or "VALID" 
    :param bias: add biases. default: False
    """
    h = tf.nn.convolution(
        x,
        filter=conv_weight_variable(ksize, x.shape[-1].value, width),
        strides=[stride] * 2,
        dilation_rate=[dilation] * 2,
        padding=padding)
    if bias:
        h += bias_variable(width)
    if print_conv:
        print(h.name, x.shape[1:], h.shape[1:])
    return h


@scoped
def separable_conv(x,
                   ksize,
                   width,
                   channel_multiplier=1,
                   stride=1,
                   padding='SAME',
                   bias=False):
    """
    A wrapper for tf.nn.conv2d.
    :param x: 4D input "NHWC" tensor 
    :param ksize: int or tuple of 2 ints representing spatial dimensions 
    :param width: number of output channels
    :param stride: int (or float in case of transposed convolution)
    :param padding: string. "SAME" or "VALID" 
    :param bias: add biases
    """
    in_width = x.shape[-1].value
    h = tf.nn.separable_conv2d(
        x,
        depthwise_filter=conv_weight_variable(
            ksize, in_width, channel_multiplier, name="wweights"),
        pointwise_filter=conv_weight_variable(
            (1, 1), in_width * channel_multiplier, width, name="pweights"),
        strides=[1] + [stride] * 2 + [1],
        padding=padding)
    if bias:
        h += bias_variable(width)
    return h


@scoped
def conv_transp(x, ksize, output_shape, stride=1, padding='SAME', bias=False):
    '''
    A wrapper for tf.nn.tf.nn.conv2d_transpose.
    :param x: 4D input "NHWC" tensor 
    :param ksize: int or tuple of 2 ints representing spatial dimensions 
    :param width: number of output channels
    :param output_shape: tuple of 2 ints
    :param padding: string. "SAME" or "VALID" 
    :param bias: add biases
    '''
    h = tf.nn.conv2d_transpose(
        x,
        filter=conv_weight_variable(ksize, output_shape[-1], x.shape[-1].value),
        output_shape=output_shape,
        strides=[1] + [stride] * 2 + [1],
        padding=padding)
    if bias:
        h += bias_variable(output_shape[-1])
    return h


@scoped
def convex_combination(x, r, scope: str = None):
    # TODO: improve initialization and constraining
    a = tf.get_variable('a', [1], initializer=tf.constant_initializer(0.99))
    constrain_a = tf.assign(a, tf.clip_by_value(a, 0, 1))
    with tf.control_dependencies([constrain_a]):
        a = tf.identity(a)  # TODO: use constraint in tf.variable_scope instead
    return a * x + (1 - a) * r


# Pooling


def max_pool(x, stride, ksize=None, padding='SAME'):
    ksize = ksize or stride
    return tf.nn.max_pool(x, [1, ksize, ksize, 1], [1, stride, stride, 1],
                          padding)


def avg_pool(x, stride, ksize=None, padding='SAME'):
    ksize = ksize or stride
    return tf.nn.avg_pool(x, [1, ksize, ksize, 1], [1, stride, stride, 1],
                          padding)


# Rescaling


def _get_rescaled_shape(x, factor):
    return (np.array([d.value
                      for d in x.shape[1:3]]) * factor + 0.5).astype(np.int)


def rescale_nearest_neighbor(x, factor):
    shape = _get_rescaled_shape(x, factor)
    return x if factor == 1 else tf.image.resize_nearest_neighbor(x, shape)


def rescale_bilinear(x, factor):
    shape = _get_rescaled_shape(x, factor)
    return x if factor == 1 else tf.image.resize_bilinear(x, shape)


# Special (batch normalization and stochastic layers)


@scoped
def batch_normalization(
        x,
        mode=None,
        is_training=None,  # has no effect if mode is defined
        offset=True,
        scale=True,
        decay=0.9,
        var_epsilon=1e-5,
        scope: str = None):
    """
    Batch normalization with scaling that normalizes over all but the last
    dimension of x.
    :param x: input tensor.
    :param mode: Tensor or str. 'moving' or 'fixed' (TODO or 'accumulation' -- 
        not implemented).
    :param is_training: Tensor or bool. Has no effect if mode is defined,
        otherwise mode is 'moving' if True and 'fixed' if False.
    :param offset: Tensor or bool. Add learned offset to the output.
    :param scale: Tensor or bool. Add learned scaling to the output.
    :param decay: Exponential moving average decay.
    """
    moving = tf.equal(mode, 'moving') if mode is not None else is_training
    assert moving is not None

    ema = tf.train.ExponentialMovingAverage(decay)
    m, v = tf.nn.moments(x, axes=list(range(len(x.shape) - 1)), name='moments')

    def m_v_with_update():
        nonlocal m, v
        with tf.control_dependencies([ema.apply([m, v])]):
            return tf.identity(m), tf.identity(v)

    mean, var = tf.cond(moving, m_v_with_update,
                        lambda: (ema.average(m), ema.average(v)))

    get_var = lambda val, name: vector_variable(x.shape[-1].value, val, name)
    offset = 0.0 if not offset else get_var(0.0, 'offset')
    scale = 1.0 if not scale else get_var(1.0, 'scale')

    return tf.nn.batch_normalization(x, mean, var, offset, scale, var_epsilon)


def dropout(x, rate, active=None, feature_map_wise=False, **kwargs):
    if rate <= 1e-5:
        return x
    if feature_map_wise:  # TODO: remove True
        kwargs = {**kwargs, 'noise_shape': [tf.shape(x)[0], 1, 1, x.shape[3]]}
    return tf.layers.dropout(x, rate=rate, **kwargs, training=active)


@scoped
def sample(fn,
           x,
           n: int,
           examplewise=False,
           max_batch_size=None,
           backprop=True,
           use_tf_loop=True):
    """
    Sampling of a stochastic operation. NOTE: it unfortunately doesn't work if 
    there are variables defined inside fn.
    :param fn: function. Stochastic operation to be sampled.
    :param x: Tensor. Input.
    :param n: number of samples; must be smaller than `max_batch_size`.
    :param examplewise: samples of elements side to side instead of batches. If
        True, output has shape `[N,n,...]` and `[n,N,...]` otherwise, where 
        `N = x.shape[0]` is the batch size.
    :param max_batch_size: int. maximum batch size to be let through `op`.
        Defaults to `n`.
    :param backprop: True enables support for back propagation.
    """

    def sample_inner(fn, x, n: int, examplewise=False):
        xr = repeat_batch(x, n, expand_dims=False)  # [nN,*,C]
        yr = fn(xr)  # [nN,*,C']
        yr = split_batch(yr, n)  # [n,N,*,C']
        if examplewise:
            transp = [1, 0] + list(range(2, len(yr.shape)))
            yr = tf.transpose(yr, transp)  # [N,n,*,C']
        return yr

    max_batch_size = max_batch_size or n
    assert n <= max_batch_size  # TODO: case when n > max_batch_size
    if use_tf_loop:
        examples_per_iteration = max(1, max_batch_size // n)
        ys = tf.map_fn(
            fn=lambda x: sample_inner(fn, x, n),  # [*,C]->[n,*,C']
            elems=x,  # [N,*,C]
            parallel_iterations=examples_per_iteration,
            back_prop=backprop)  # [N,n,*,C']
        if not examplewise:  # [N,n,*,C']
            transp = [1, 0] + list(range(2, len(ys.shape)))
            ys = tf.transpose(ys, transp)  # [n,N,*,C']
        return ys
    else:
        return sample_inner(fn, x, n, examplewise=examplewise)


def normal_noise(stddev):
    return tf.random_normal(shape=stddev.shape, stddev=stddev)


# Blocks


@scoped
def bn_relu(x, bn_params=dict()):
    x = batch_normalization(x, **bn_params, name='bn')
    return tf.nn.relu(x)


@scoped
def identity_mapping(x, stride, width):
    x_width = x.shape[-1].value
    assert width >= x_width
    if stride > 1:
        x = avg_pool(x, stride, padding='SAME')
    if width > x_width:
        x = tf.pad(x, 3 * [[0, 0]] + [[0, width - x_width]])
    return x


class BlockStructure:

    def __init__(self, ksizes, width_factors, dropout_locations):
        """
        :param ksizes: list of ints or int pairs. kernel sizes of convolutional 
            layers
        :param dropout_locations: indexes of convolutional layers after which
            dropout is to be applied
        :param width_factors: int or list[int]
        """
        self.ksizes = ksizes
        self.width_factors = width_factors
        if type(width_factors) is int:
            self.width_factors = [width_factors for _ in ksizes]
        elif len(ksizes) != len(width_factors):
            raise ValueError(
                "width_factors must be an integer or a list of equal length as ksizes"
            )
        if dropout_locations == 'all':
            dropout_locations = list(range(len(ksizes)))
        self.dropout_locations = dropout_locations

    @classmethod
    def resnet(cls, ksizes=[3, 3], width_factors=1, dropout_locations=[0]):
        return BlockStructure(**filter_dict(locals(), lambda k: k != 'cls'))

    @classmethod
    def resnet_bottleneck(cls,
                          ksizes=[1, 3, 1],
                          width_factors=[1, 1, 4],
                          dropout_locations=[0]):
        return BlockStructure(**filter_dict(locals(), lambda k: k != 'cls'))

    @classmethod
    def densenet(cls,
                 ksizes=[1, 3],
                 width_factors=[4, 1],
                 dropout_locations='all'):
        return BlockStructure(**filter_dict(locals(), lambda k: k != 'cls'))


@scoped
def block(x,
          structure=BlockStructure.resnet(),
          stride=1,
          base_width=16,
          omit_first_bn_relu=False,
          bn_params=dict(),
          dropout_params=dict()):
    s = structure
    for i, (ksize, wf) in enumerate(zip(s.ksizes, s.width_factors)):
        if not omit_first_bn_relu:
            x = bn_relu(x, bn_params, name=f'bn_relu{i}')
        omit_first_bn_relu = False
        x = conv(
            x,
            ksize,
            base_width * wf,
            stride=stride if i == 0 else 1,
            bias=False,
            name=f'conv{i}')
        if i in s.dropout_locations:
            x = dropout(x, **dropout_params)
    return x


@scoped
def residual_block(x,
                   structure=BlockStructure.resnet(),
                   stride=1,
                   width=16,
                   dim_change='id',
                   bn_params=dict(),
                   dropout_params={'rate': 0.3}):
    """
    A generic ResNet full pre-activation residual block.
    :param x: input tensor
    :param structure: a BlockStructure instance
    :param width: number of output channels
    :param bn_params: parameters for `batch_normalization`
    :param dropout_params: parameters for `dropout`
    """
    x_width = x.shape[-1].value
    dim_changing = width * structure.width_factors[-1] > x_width or stride > 1
    skip_connection_needs_bn_relu = dim_changing and dim_change == 'proj'

    def _skip_connection(x, stride, width):
        if dim_changing:
            if dim_change == 'id':
                x = identity_mapping(x, stride, width)
            elif dim_change == 'proj':  # He
                x = conv(
                    x, 1, width, stride=stride, bias=False, name='conv_skip')
        return x

    if skip_connection_needs_bn_relu:
        x = bn_relu(x, bn_params)
    r = block(
        x,
        structure,
        stride=stride,
        base_width=width,
        omit_first_bn_relu=skip_connection_needs_bn_relu,
        bn_params=bn_params,
        dropout_params=dropout_params,
        name='block')
    s = _skip_connection(x, stride, r.shape[-1].value)
    return s + r


@scoped
def densenet_transition(x,
                        compression: float = 0.5,
                        pool_func=avg_pool,
                        bn_params=dict(),
                        dropout_params=dict()):
    """ Densenet transition layer """
    x = bn_relu(x, bn_params)
    width = int(x.shape[-1].value * compression)  # floor (page 4.)
    x = conv(x, 1, width, bias=False)
    if dropout_params.get('rate', 0) >= 1e-6:
        x = dropout(x, **dropout_params)
    x = pool_func(x, stride=2)
    return x


@scoped
def dense_block(x,
                length,
                block_structure=BlockStructure.densenet(),
                base_width=12,
                bn_params=dict(),
                dropout_params=dict()):
    """
    A generic dense block.
    :param x: input tensor
    :param length: number of elementary blocks
    :param block_structure: a BlockStructure instance for elementary blocks
    :param base_width: number of output channels of an elementary block
    :param bn_params: parameters for `batch_normalization`
    :param dropout_params: parameters for `dropout`
    """
    for i in range(length):
        a = block(
            x,
            block_structure,
            base_width=base_width,
            bn_params=bn_params,
            dropout_params=dropout_params,
            name=f'block{i}')
        x = tf.concat([x, a], axis=3)
    return x


# Components


@scoped
def resnet_root_block(x, base_width=16, cifar_type=False):
    """
    A pre-activation resnet without the final global pooling and classification
    layers.
    :param x: input tensor
    :param base_width: number of output channels
    :param cifar_type: If True 3x3 instead of 7x7 convolution is used and 
        max_pool is omitted. 
    """
    if cifar_type:
        x = conv(x, 3, base_width, bias=False)
    else:
        x = conv(x, 7, base_width, stride=2, bias=False)
        x = max_pool(x, stride=2, ksize=3)
    return x


@scoped
def resnet_middle(x,
                  base_width=16,
                  width_factor=1,
                  group_lengths=[2] * 3,
                  block_structure=BlockStructure.resnet(),
                  dim_change='id',
                  bn_params=dict(),
                  dropout_params={'rate': 0},
                  pretrained_lr_factor=1):
    """
    A pre-activation resnet without the root block and final global pooling and 
    classification layers.
    :param x: input tensor
    :param base_width: number of output channels of layers in the first group
    :param group_lengths: numbers of blocks per group
    :param block_structure: a BlockStructure instance
    :param bn_params: parameters for `batch_normalization`
    :param dropout_params: parameters for `dropout`
    """
    assert 'is_training' in bn_params
    assert 'active' in dropout_params and 'rate' in dropout_params
    base_width *= width_factor
    x = amplify_gradient(x, 1 / pretrained_lr_factor)
    for i, length in enumerate(group_lengths):
        group_width = 2**i * base_width
        with tf.variable_scope(f'group{i}'):
            for j in range(length):
                x = residual_block(
                    x,
                    structure=block_structure,
                    stride=1 + int(i > 0 and j == 0),
                    width=group_width,
                    dim_change=dim_change,
                    bn_params=bn_params,
                    dropout_params=dropout_params,
                    name=f'rb{j}')
    x = bn_relu(x, bn_params, name="post_bn_relu")
    return amplify_gradient(x, pretrained_lr_factor)


@scoped
def densenet_root_block(x, base_width=16, cifar_type=False):
    """
    A pre-activation resnet without the final global pooling and classification
    layers.
    :param x: input tensor
    :param base_width: 1/2 the number of output channels (densenet growth rate)
    :param cifar_type: If True 3x3 instead of 7x7 convolution is used and 
        max_pool is omitted. 
    """
    if cifar_type:
        x = conv(x, 3, 2 * base_width, bias=False)
    else:
        x = conv(x, 7, 2 * base_width, stride=2, bias=False)
        x = max_pool(x, stride=2, ksize=3)
    return x


@scoped
def densenet_middle(
        x,
        base_width,
        group_lengths,  # dense block sizes
        block_structure=BlockStructure.densenet(),
        compression=0.5,
        bn_params=dict(),
        dropout_params=dict(),  # 0.2 if data augmentation is not used
        pretrained_lr_factor=1):
    """
    A densenet without the root block and final global pooling and 
    classification layers.
    :param x: input tensor
    :param base_width: number of output channels of the first layer
    :param group_lengths: numbers of elementary blocks per dense block
    :param block_structure: a BlockStructure instance
    :param compression: float from 0 to 1. transition layer compression
    :param bn_params: parameters for `batch_normalization`
    :param dropout_params: parameters for `dropout`
    """
    assert 'is_training' in bn_params
    assert 'active' in dropout_params and 'rate' in dropout_params
    x = amplify_gradient(x, 1 / pretrained_lr_factor)
    for i, length in enumerate(group_lengths):
        if i > 0:
            x = densenet_transition(
                x,
                compression,
                bn_params=bn_params,
                dropout_params=dropout_params,
                name=f'transition{i-1}')
        x = dense_block(
            x,
            length=length,
            block_structure=block_structure,
            base_width=base_width,
            bn_params=bn_params,
            dropout_params=dropout_params,
            name=f'db{i}')
    x = bn_relu(x, bn_params, name="post_bn_relu")
    return amplify_gradient(x, pretrained_lr_factor)


@scoped
def ladder_densenet(
        x,
        base_width=32,
        group_lengths=[6, 12, 24, 16],  # 121 [6, 12, 32, 32]-169
        block_structure=BlockStructure.densenet(dropout_locations=[1]),
        compression=0.5,
        upsampling_block_width=128,
        baseline=False,
        bn_params=dict(),
        dropout_params=dict(),  # or 0.2
        cifar_root_block=False,
        pretrained_lr_factor=1):
    """
    A Ladder-DenseNet up to pre-logits layers.
    :param x: input tensor
    :param base_width: number of output channels of the first layer
    :param group_lengths: numbers of elementary blocks per dense block
    :param block_structure: a BlockStructure instance
    :param compression: float from 0 to 1. transition layer compression
    :param bn_params: parameters for `batch_normalization`
    :param dropout_params: parameters for `dropout`
    """
    db_params = filter_dict(locals(), \
        ['block_structure', 'base_width', 'bn_params', 'dropout_params'])
    tr_params = filter_dict(locals(),
                            ['compression', 'bn_params', 'dropout_params'])

    def bn_relu_conv_dropout(x, ksize, width, dilation=1, name=None):
        x = bn_relu(x, bn_params)  # TODO name
        x = conv(x, ksize, width, bias=False, dilation=dilation, name=name)
        return dropout(x, **dropout_params)

    @scoped
    def blend_up(x, skip):
        x = tf.image.resize_bilinear(x, tf.shape(skip)[1:3])
        skip = bn_relu_conv_dropout(
            skip, 1, x.shape[-1].value, name=f'conv_bottleneck{i}')
        x = tf.concat([x, skip], axis=3)
        return bn_relu_conv_dropout(
            x, 3, upsampling_block_width, name=f'conv_blend{i}')

    # DenseNet start
    x = amplify_gradient(x, 1 / pretrained_lr_factor)
    x = densenet_root_block(x, base_width, cifar_type=cifar_root_block)

    # DenseNet bloks with the 4-th being split in the middle with avg. pooling
    skips = []
    for i, length in enumerate(group_lengths[:-1]):
        x = dense_block(x, length=length, **db_params, name=f'db{i}')
        skips.append(amplify_gradient(x, pretrained_lr_factor))
        x = densenet_transition(x, **tr_params, name=f'transition{i}')
    i, length = len(group_lengths) - 1, group_lengths[-1]
    x = dense_block(x, length=length // 2, **db_params, name=f'db{i}a')
    skips.append(amplify_gradient(x, pretrained_lr_factor))
    x = avg_pool(x, 2)
    x = dense_block(x, length=length - length // 2, **db_params, name=f'db{i}b')
    x = amplify_gradient(x, pretrained_lr_factor)

    with tf.variable_scope('head'):
        x = bn_relu_conv_dropout(x, 1, 128 * 4, name='bottleneck/conv')
        x = bn_relu_conv_dropout(x, 3, 128, dilation=2, name='context/conv')

        if baseline:
            return bn_relu(x)

        # The upsampling path
        pre_logits_aux = bn_relu(x, bn_params)  # no bn in the original code
        for i, skip in reversed(list(enumerate(skips))):
            x = blend_up(x, skip, name=f'blend{i}')
        pre_logits = bn_relu(x, bn_params)

        return pre_logits, pre_logits_aux
