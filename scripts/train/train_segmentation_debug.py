import os
import sys
import shutil
import argparse
from tqdm import tqdm

import torch
from torch import optim
from torch.backends import cudnn
from torch.utils import data
from torchvision import transforms

cur_path = os.path.dirname(__file__)
sys.path.insert(0, os.path.join(cur_path, '../..'))
import utils as ptutil
from utils.metrics import SegmentationMetric
from data import get_segmentation_dataset
from data.base import make_data_sampler
from model.loss import MixSoftmaxCrossEntropyLoss
from model.lr_scheduler import LRScheduler
from model.model_zoo import get_model
from model.models_zoo import get_segmentation_model


class Trainer(object):
    def __init__(self, args, device, distributed):
        self.args = args
        self.device = device
        # image transform
        input_transform = transforms.Compose([
            transforms.ToTensor(),
            transforms.Normalize([.485, .456, .406], [.229, .224, .225]),
        ])
        # dataset and dataloader
        data_kwargs = {'transform': input_transform, 'base_size': args.base_size,
                       'crop_size': args.crop_size}
        trainset = get_segmentation_dataset(
            args.dataset, split=args.train_split, mode='train', **data_kwargs)
        train_sampler = make_data_sampler(trainset, False, distributed)
        train_batch_sampler = data.sampler.BatchSampler(train_sampler, args.batch_size, True)
        self.train_data = data.DataLoader(trainset, batch_sampler=train_batch_sampler,
                                          num_workers=args.workers)
        valset = get_segmentation_dataset(args.dataset, split='val', mode='val', **data_kwargs)
        val_sampler = make_data_sampler(valset, False, distributed)
        val_batch_sampler = data.sampler.BatchSampler(val_sampler, args.test_batch_size, False)

        self.eval_data = data.DataLoader(valset, batch_sampler=val_batch_sampler,
                                         num_workers=args.workers)

        # create network
        BatchNorm2d = torch.nn.SyncBatchNorm if distributed else torch.nn.BatchNorm2d
        if args.model_zoo is not None:
            self.net = get_model(args.model_zoo, pretrained=True, norm_layer=BatchNorm2d)
        else:
            self.net = get_segmentation_model(model=args.model, dataset=args.dataset,
                                              backbone=args.backbone, norm_layer=BatchNorm2d,
                                              norm_kwargs={}, aux=args.aux,
                                              crop_size=args.crop_size)
        self.net.to(device)
        # resume checkpoint if needed
        if args.resume is not None:
            if os.path.isfile(args.resume):
                self.net.load_state_dict(torch.load(args.resume))
            else:
                raise RuntimeError("=> no checkpoint found at '{}'" \
                                   .format(args.resume))

        if distributed:
            self.net = torch.nn.parallel.DistributedDataParallel(
                self.net, device_ids=[args.local_rank], output_device=args.local_rank)

        # create criterion
        self.criterion = MixSoftmaxCrossEntropyLoss(args.aux, aux_weight=args.aux_weight)
        # optimizer and lr scheduling
        self.optimizer = optim.SGD(self.net.parameters(), lr=args.lr, momentum=args.momentum,
                                   weight_decay=args.weight_decay)
        self.lr_scheduler = LRScheduler(self.optimizer, mode='poly',
                                        n_iters=len(self.train_data),
                                        n_epochs=args.epochs)

        # if args.no_wd:
        #     for k, v in self.net.module.collect_params('.*beta|.*gamma|.*bias').items():
        #         v.wd_mult = 0.0

        # evaluation metrics
        self.metric = SegmentationMetric(trainset.num_class)

    def training(self, epoch):
        self.net.train()
        tbar = tqdm(self.train_data)
        train_loss = 0.0
        for i, (image, target) in enumerate(tbar):
            image, target = image.to(self.device), target.to(self.device)
            self.lr_scheduler.step(i, epoch)
            outputs = self.net(image)
            losses = self.criterion(outputs, target)
            self.optimizer.zero_grad()
            losses.backward()
            self.optimizer.step()
            train_loss += losses.item()

            if ptutil.is_main_process():
                tbar.set_description('Epoch %d, training loss %.3f' % \
                                     (epoch, train_loss / (i + 1)))
        # save every epoch
        if ptutil.is_main_process():
            self.save_checkpoint(False)
        return train_loss

    def validation(self):
        # total_inter, total_union, total_correct, total_label = 0, 0, 0, 0
        self.metric.reset()
        self.net.eval()
        tbar = tqdm(self.eval_data)
        for i, (image, target) in enumerate(tbar):
            image, target = image.to(self.device), target.to(self.device)
            with torch.no_grad():
                outputs = self.net.evaluate(image)
            self.metric.update(target, outputs)
        return self.metric

    def save_checkpoint(self, is_best=False):
        """Save Checkpoint"""
        directory = "runs/%s/%s/%s/" % (args.dataset, args.model, args.checkname)
        if not os.path.exists(directory):
            os.makedirs(directory)
        filename = 'checkpoint.params'
        filename = directory + filename
        torch.save(self.net.state_dict(), filename)
        if is_best:
            shutil.copyfile(filename, directory + 'model_best.params')


def parse_args():
    """Training Options for Segmentation Experiments"""
    parser = argparse.ArgumentParser(description='PyTorch Segmentation')
    # model and dataset
    parser.add_argument('--model', type=str, default='fcn',
                        help='model name (default: fcn)')
    parser.add_argument('--backbone', type=str, default='resnet50',
                        help='backbone name (default: resnet50)')
    parser.add_argument('--dataset', type=str, default='ade20k',
                        help='dataset name (default: ade20k)')
    parser.add_argument('--workers', '-j', type=int, default=4,
                        metavar='N', help='dataloader threads')
    parser.add_argument('--base-size', type=int, default=520,
                        help='base image size')
    parser.add_argument('--crop-size', type=int, default=480,
                        help='crop image size')
    parser.add_argument('--train-split', type=str, default='train',
                        help='dataset train split (default: train)')
    # training hyper params
    parser.add_argument('--aux', action='store_true', default=False,
                        help='Auxiliary loss')
    parser.add_argument('--aux-weight', type=float, default=0.5,
                        help='auxiliary loss weight')
    parser.add_argument('--epochs', type=int, default=50, metavar='N',
                        help='number of epochs to train (default: 50)')
    parser.add_argument('--start_epoch', type=int, default=0,
                        metavar='N', help='start epochs (default:0)')
    parser.add_argument('--batch-size', type=int, default=1,
                        metavar='N', help='input batch size for \
                        training (default: 16)')
    parser.add_argument('--test-batch-size', type=int, default=1,
                        metavar='N', help='input batch size for \
                        testing (default: 16)')
    parser.add_argument('--lr', type=float, default=1e-3, metavar='LR',
                        help='learning rate (default: 1e-3)')
    parser.add_argument('--momentum', type=float, default=0.9,
                        metavar='M', help='momentum (default: 0.9)')
    parser.add_argument('--weight-decay', type=float, default=1e-4,
                        metavar='M', help='w-decay (default: 1e-4)')
    parser.add_argument('--no-wd', action='store_true',
                        help='whether to remove weight decay on bias, \
                        and beta/gamma for batchnorm layers.')
    # cuda and logging
    parser.add_argument('--no-cuda', action='store_true', default=False,
                        help='disables CUDA training')
    parser.add_argument('--local_rank', type=int, default=0)
    parser.add_argument('--dtype', type=str, default='float32',
                        help='data type for training. default is float32')
    # checking point
    parser.add_argument('--resume', type=str, default=None,
                        help='put the path to resuming file if needed')
    parser.add_argument('--checkname', type=str, default='res50',  # 'default'
                        help='set the checkpoint name')
    parser.add_argument('--model-zoo', type=str, default=None,
                        help='evaluating on model zoo model')
    # evaluation only
    parser.add_argument('--eval', action='store_true', default=False,
                        help='evaluation only')
    parser.add_argument('--no-val', action='store_true', default=False,
                        help='skip validation during training')

    # the parser
    args = parser.parse_args()
    return args


if __name__ == "__main__":
    args = parse_args()

    device = torch.device('cpu')
    num_gpus = int(os.environ["WORLD_SIZE"]) if "WORLD_SIZE" in os.environ else 1
    distributed = num_gpus > 1
    if not args.no_cuda and torch.cuda.is_available():
        cudnn.benchmark = True
        device = torch.device('cuda')
    else:
        distributed = False

    if distributed:
        torch.cuda.set_device(args.local_rank)
        torch.distributed.init_process_group(backend="nccl", init_method="env://")
        ptutil.synchronize()

    trainer = Trainer(args, device, distributed)
    if args.eval:
        if ptutil.is_main_process():
            print('Evaluating model: ', args.resume)
        trainer.validation(args.start_epoch)
    else:
        if ptutil.is_main_process():
            print('Starting Epoch:', args.start_epoch)
            print('Total Epochs:', args.epochs)
        for epoch in range(args.start_epoch, args.epochs):
            train_loss = trainer.training(epoch)
            if not args.no_val:
                metric = trainer.validation(epoch)
            ptutil.synchronize()
            train_loss = ptutil.reduce_list(ptutil.all_gather(train_loss), average=False)
            if ptutil.is_main_process():
                print(('Epoch %d, training loss %.3f' % \
                       (epoch, train_loss / len(trainer.train_data.dataset))))
            if not args.no_val:
                pixAcc, mIoU = ptutil.accumulate_metric(metric)
                if ptutil.is_main_process():
                    print('Epoch %d, validation pixAcc: %.3f, mIoU: %.3f' % \
                          (epoch, pixAcc, mIoU))
