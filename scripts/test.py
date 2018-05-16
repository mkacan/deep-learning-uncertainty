import os
import argparse
import datetime

import numpy as np

from _context import dl_uncertainty

from dl_uncertainty import dirs, training
from dl_uncertainty import data_utils, model_utils
from dl_uncertainty.visualization import view_semantic_segmentation

# Use "--trainval" only for training on "trainval" and testing "test".
# CUDA_VISIBLE_DEVICES=0 python test.py
# CUDA_VISIBLE_DEVICES=1 python test.py
# CUDA_VISIBLE_DEVICES=2 python test.py
#   cifar wrn 28 10
#   cifar dn 100 12
#   cifar rn 34 8
#   cityscapes dn 121 32
#   cityscapes rn 50 64
#   cityscapes ldn 121 32
#   mozgalo rn 50 64
#   mozgalo rn 18 64

parser = argparse.ArgumentParser()
parser.add_argument('ds', type=str)
parser.add_argument('net', type=str)  # 'wrn' or 'dn'
parser.add_argument('depth', type=int)
parser.add_argument('width', type=int)
parser.add_argument('saved_path', type=str)
parser.add_argument('--trainval', action='store_true')
parser.add_argument('--display', action='store_true')
parser.add_argument('--hard', action='store_true')  # display hard exampels
args = parser.parse_args()
print(args)

# Cached dataset with normalized inputs

print("Setting up data loading...")
ds_train, ds_test = data_utils.get_cached_dataset_with_normalized_inputs(
    args.ds, trainval_test=args.trainval)
problem_id = ds_train.info['problem_id']

# Model

print("Initializing model...")
model = model_utils.get_model(
    net_name=args.net,
    problem_id=problem_id,
    epoch_count=0,
    ds_id=args.ds,
    ds_train=ds_train,
    depth=args.depth,
    width=args.width)

model.load_state(args.saved_path)

if args.display or args.hard:
    ds_disp = training.get_hard_examples(model, ds_test) \
              if args.hard else ds_test
    view_semantic_segmentation(ds_disp, lambda x: model.predict([x])[0])
else:
    model.test(ds_train, "Training set")
    model.test(ds_test, "Test set")