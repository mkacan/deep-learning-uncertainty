import tensorflow as tf
import numpy as np

from .ioutils import console
from .data import Dataset, DataLoader
from .models import Model
from . import dirs
from .visualization import view_semantic_segmentation


def train(model: Model,
          ds_train: Dataset,
          ds_val: Dataset,
          input_jitter=None,
          epoch_count=200,
          data_loading_worker_count=0):

    ds_view = ds_val

    def handle_step(i):
        text = console.read_line(impatient=True, discard_non_last=True)
        if text == 'q':
            return True
        elif text == 's':
            writer = tf.summary.FileWriter(dirs.LOGS, graph=model._sess.graph)
        elif text == 'd':
            view_semantic_segmentation(ds_view, lambda x: model.predict([x])[0])
        return False

    model.training_step_event_handler = handle_step

    ds_train_part = ds_train.permute().subset(np.arange(len(ds_val)))
    ds_train = ds_train.map(input_jitter, 0)

    ds_train, ds_val, ds_train_part = [
        DataLoader(
            ds,
            batch_size=model.batch_size,
            shuffle=True,
            num_workers=data_loading_worker_count,
            drop_last=True) for ds in [ds_train, ds_val, ds_train_part]
    ]

    model.test(ds_val)
    for i in range(epoch_count):
        model.train(ds_train, epoch_count=1)
        model.test(ds_val, 'validation data')
        model.test(ds_train_part, 'training data subset')