#!/usr/bin/env bash

# -----------------------------------------------------------------------------
# Classification
# -----------------------------------------------------------------------------
# #----------------------------cifar10----------------------------
export NGPUS=2
srun --partition=HA_3D --mpi=pmi2 --gres=gpu:2 -n1 --ntasks-per-node=8 python -m torch.distributed.launch --nproc_per_node=$NGPUS eval/eval_cifar10.py --network CIFAR_ResNet20_v1 --batch-size 8 --cuda
#python eval/eval_cifar10.py --network CIFAR_ResNet56_v1 --batch-size 8
#python eval/eval_cifar10.py --network CIFAR_ResNet110_v1 --batch-size 8
#python eval/eval_cifar10.py --network CIFAR_ResNet20_v2 --batch-size 8
#python eval/eval_cifar10.py --network CIFAR_ResNet56_v2 --batch-size 8
#python eval/eval_cifar10.py --network CIFAR_ResNet110_v2 --batch-size 8
#python eval/eval_cifar10.py --network CIFAR_ResNet20_v2 --batch-size 8
#python eval/eval_cifar10.py --network CIFAR_WideResNet16_10 --batch-size 8
#python eval/eval_cifar10.py --network CIFAR_WideResNet28_10 --batch-size 8
#python eval/eval_cifar10.py --network CIFAR_WideResNet40_8 --batch-size 8
#python eval/eval_cifar10.py --network CIFAR_ResNeXt29_16x64d --batch-size 8
