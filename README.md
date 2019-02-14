# pytorch-cv

Convert the [gluon-cv](https://github.com/dmlc/gluon-cv/) to pytorch. 

## Usage

1. using [gluon2torch](./utils/gluon2torch) to convert pretrained gluon model to pytorch

   > - `base=False` in classification and `True` in detection
   > - `reorder=True` in resnet+ssd, others is `False`

2. run demo 

## Demo

### Classification

- [x] CIFAR-10
- [x] ImageNet

### Detection

- [x] SSD（VOC）
- [x] YOLO（VOC）