import argparse
import os
import shutil
import time
import random
import numpy as np

import torch
import torch.nn as nn
import torch.nn.parallel
import torch.backends.cudnn as cudnn
import torch.optim
import torch.utils.data
import torchvision.transforms as transforms
import torchvision.datasets as datasets
import torchvision.models as models
from MODELS.model_resnet import *
from PIL import ImageFile, Image

ImageFile.LOAD_TRUNCATED_IMAGES = True
model_names = sorted(name for name in models.__dict__
    if name.islower() and not name.startswith("__")
    and callable(models.__dict__[name]))

parser = argparse.ArgumentParser(description='PyTorch ImageNet Training')
parser.add_argument('data', metavar='DIR',
                    help='path to dataset')
parser.add_argument('--arch', '-a', metavar='ARCH', default='resnet',
                    help='model architecture: ' +
                        ' | '.join(model_names) +
                        ' (default: resnet18)')
parser.add_argument('--whitened_layers', default='1,2,3')
parser.add_argument('--depth', default=50, type=int, metavar='D',
                    help='model depth')
parser.add_argument('--ngpu', default=4, type=int, metavar='G',
                    help='number of gpus to use')
parser.add_argument('-j', '--workers', default=4, type=int, metavar='N',
                    help='number of data loading workers (default: 4)')
parser.add_argument('--epochs', default=100, type=int, metavar='N',
                    help='number of total epochs to run')
parser.add_argument('--start-epoch', default=0, type=int, metavar='N',
                    help='manual epoch number (useful on restarts)')
parser.add_argument('-b', '--batch-size', default=256, type=int,
                    metavar='N', help='mini-batch size (default: 256)')
parser.add_argument('--lr', '--learning-rate', default=0.1, type=float,
                    metavar='LR', help='initial learning rate')
parser.add_argument('--momentum', default=0.9, type=float, metavar='M',
                    help='momentum')
parser.add_argument('--weight-decay', '--wd', default=5e-4, type=float,
                    metavar='W', help='weight decay (default: 1e-4)')
parser.add_argument('--print-freq', '-p', default=10, type=int,
                    metavar='N', help='print frequency (default: 10)')
parser.add_argument('--resume', default='', type=str, metavar='PATH',
                    help='path to latest checkpoint (default: none)')
parser.add_argument("--seed", type=int, default=1234, metavar='BS', help='input batch size for training (default: 64)')
parser.add_argument("--prefix", type=str, required=True, metavar='PFX', help='prefix for logging & checkpoint saving')
parser.add_argument('--evaluate', dest='evaluate', action='store_true', help='evaluation only')
parser.add_argument('--att-type', type=str, choices=['BAM', 'CBAM'], default='CBAM')
best_prec1 = 0

if not os.path.exists('./checkpoints'):
    os.mkdir('./checkpoints')

class ImageFolderWithPaths(datasets.ImageFolder):
    """Custom dataset that includes image file paths. Extends
    torchvision.datasets.ImageFolder
    """

    # override the __getitem__ method. this is the method that dataloader calls
    def __getitem__(self, index):
        # this is what ImageFolder normally returns 
        original_tuple = super(ImageFolderWithPaths, self).__getitem__(index)
        # the image file path
        path = self.imgs[index][0]
        # make a new tuple that includes original and the path
        tuple_with_path = (original_tuple + (path,))
        return tuple_with_path

def get_param_list(model, whitened_layers):
    param_list = list(model.model.fc.parameters())
    layers = model.layers
    for whitened_layer in whitened_layers:
        if whitened_layer <= layers[0]:
            param_list += list(model.model.layer1[whitened_layer-1].bn1.parameters())
        elif whitened_layer <= layers[0] + layers[1]:
            param_list += list(model.model.layer2[whitened_layer-layers[0]-1].bn1.parameters())
        elif whitened_layer <= layers[0] + layers[1] + layers[2]:
            param_list += list(model.model.layer3[whitened_layer-layers[0]-layers[1]-1].bn1.parameters())
        elif whitened_layer <= layers[0] + layers[1] + layers[2] + layers[3]:
            param_list += list(model.model.layer4[whitened_layer-layers[0]-layers[1]-layers[2]-1].bn1.parameters())
    return param_list


def main():
    global args, best_prec1
    global viz, train_lot, test_lot
    args = parser.parse_args()
    print ("args", args)

    torch.manual_seed(args.seed)
    torch.cuda.manual_seed_all(args.seed)
    random.seed(args.seed)

    args.prefix += '_'+'_'.join(args.whitened_layers.split(','))

    # create model
    if args.arch == "resnet":
        model = ResidualNet( 'ImageNet', args.depth, 9, None, [int(x) for x in args.whitened_layers.split(',')])
    elif args.arch == "resnet_transfer":
        model = ResidualNetTransfer(9, [int(x) for x in args.whitened_layers.split(',')], model_file ='./checkpoints/RESNET18_PLACES_VANILLA_model_best.pth.tar')
    # define loss function (criterion) and optimizer
    criterion = nn.CrossEntropyLoss().cuda()
    param_list = get_param_list(model, [int(x) for x in args.whitened_layers.split(',')])
    # optimizer = torch.optim.SGD(model.parameters(), args.lr,
    #                         momentum=args.momentum,
    #                         weight_decay=args.weight_decay)
    optimizer = torch.optim.SGD(param_list, args.lr,
                            momentum=args.momentum,
                            weight_decay=args.weight_decay)

    # optimizer = torch.optim.Adam(model.parameters(), lr = args.lr,
    #                         weight_decay=args.weight_decay)
                            
    model = torch.nn.DataParallel(model, device_ids=list(range(args.ngpu)))
    #model = torch.nn.DataParallel(model).cuda()
    model = model.cuda()
    print ("model")
    print (model)

    # get the number of model parameters
    print('Number of model parameters: {}'.format(
        sum([p.data.nelement() for p in model.parameters()])))

    # optionally resume from a checkpoint
    if args.resume:
        args.resume = args.resume[:-19] + '_' + '_'.join(args.whitened_layers.split(',')) + args.resume[-19:]
        if os.path.isfile(args.resume):
            print("=> loading checkpoint '{}'".format(args.resume))
            checkpoint = torch.load(args.resume)
            args.start_epoch = checkpoint['epoch']
            best_prec1 = checkpoint['best_prec1']
            model.load_state_dict(checkpoint['state_dict'])
            if 'optimizer' in checkpoint:
                optimizer.load_state_dict(checkpoint['optimizer'])
            print("=> loaded checkpoint '{}' (epoch {})"
                  .format(args.resume, checkpoint['epoch']))
        else:
            print("=> no checkpoint found at '{}'".format(args.resume))
    
    print(best_prec1)
    cudnn.benchmark = True

    # Data loading code
    traindir = os.path.join(args.data, 'train')
    valdir = os.path.join(args.data, 'val')
    conceptdir = os.path.join(args.data, 'concept')
    normalize = transforms.Normalize(mean=[0.485, 0.456, 0.406],
                                 std=[0.229, 0.224, 0.225])

    # import pdb
    # pdb.set_trace()
    train_loader = torch.utils.data.DataLoader(
        datasets.ImageFolder(traindir, transforms.Compose([
            transforms.RandomSizedCrop(224),
            transforms.RandomHorizontalFlip(),
            transforms.ToTensor(),
            normalize,
        ])),
        batch_size=args.batch_size, shuffle=True,
        num_workers=args.workers, pin_memory=True)

    concept_loader = torch.utils.data.DataLoader(
        datasets.ImageFolder(conceptdir, transforms.Compose([
            transforms.RandomSizedCrop(224),
            transforms.RandomHorizontalFlip(),
            transforms.ToTensor(),
            normalize,
        ])),
        batch_size=args.batch_size, shuffle=True,
        num_workers=args.workers, pin_memory=True)

    val_loader = torch.utils.data.DataLoader(
        datasets.ImageFolder(valdir, transforms.Compose([
            transforms.Scale(256),
            transforms.CenterCrop(224),
            transforms.ToTensor(),
            normalize,
        ])),
        batch_size=args.batch_size, shuffle=False,
        num_workers=args.workers, pin_memory=True)
    
    val_loader_2 = torch.utils.data.DataLoader(
        ImageFolderWithPaths(valdir, transforms.Compose([
            transforms.Scale(256),
            transforms.CenterCrop(224),
            transforms.ToTensor(),
            normalize,
        ])),
        batch_size=args.batch_size, shuffle=False,
        num_workers=args.workers, pin_memory=True)
    # # Cifar 10
    # transform_train = transforms.Compose([
    #     transforms.RandomCrop(32, padding=4),
    #     transforms.RandomHorizontalFlip(),
    #     transforms.ToTensor(),
    #     transforms.Normalize((0.4914, 0.4822, 0.4465), (0.2023, 0.1994, 0.2010)),
    # ])

    # transform_test = transforms.Compose([
    #     transforms.ToTensor(),
    #     transforms.Normalize((0.4914, 0.4822, 0.4465), (0.2023, 0.1994, 0.2010)),
    # ])

    # trainset = datasets.CIFAR10(root=args.data, train=True, download=True, transform=transform_train)
    # train_loader = torch.utils.data.DataLoader(trainset, batch_size=args.batch_size, shuffle=True, num_workers=args.workers)

    # testset = datasets.CIFAR10(root=args.data, train=False, download=True, transform=transform_test)
    # val_loader = torch.utils.data.DataLoader(testset, batch_size=args.batch_size, shuffle=False, num_workers=args.workers)

    # if args.evaluate:
    #      #validate(val_loader, model, criterion, 0)
    #      print(best_prec1)
    #      mean = get_optimal_direction(concept_loader, model, args.whitened_layers)
    #      rotate_cpt(concept_loader, model, mean)
    #      print_concept_top5(val_loader_2, model, args.whitened_layers)
    #      return

    # from PIL import Image
    # for i, (input, target) in enumerate(train_loader):
    #     if i == 2:
    #         break
    #     x = input.cpu().numpy()
    #     y = target.cpu().numpy()
    #     #print(y)
    #     for j, xx in enumerate(x):
    #         img = Image.fromarray(xx[0]*255).convert('L')
    #         img.save('/usr/xtmp/zhichen/attention-module/plot/train'+str(i)+str(j)+'_'+str(y[j])+'.png')

    # for i, (input, target) in enumerate(val_loader):
    #     if i == 2:
    #         break
    #     x = input.cpu().numpy()
    #     y = target.cpu().numpy()
    #     for j, xx in enumerate(x):
    #         img = Image.fromarray(xx[0]*255).convert('L')
    #         img.save('/usr/xtmp/zhichen/attention-module/plot/val'+str(i)+str(j)+'_'+str(y[j])+'.png')

    for epoch in range(args.start_epoch, args.start_epoch + 5):
    #for epoch in range(args.start_epoch, args.epochs):
        
        adjust_learning_rate(optimizer, epoch)
        
        # train for one epoch
        train(train_loader, concept_loader, model, criterion, optimizer, epoch)
        
        # evaluate on validation set
        prec1 = validate(val_loader, model, criterion, epoch)
        
        # remember best prec@1 and save checkpoint
        is_best = prec1 > best_prec1
        best_prec1 = max(prec1, best_prec1)
        save_checkpoint({
            'epoch': epoch + 1,
            'arch': args.arch,
            'state_dict': model.state_dict(),
            'best_prec1': best_prec1,
            'optimizer' : optimizer.state_dict(),
        }, is_best, args.prefix)
    print_concept_top5(val_loader_2, model, args.whitened_layers)

def get_optimal_direction(concept_loader, model, whitened_layers):
    n = 0
    layer_list = whitened_layers.split(',')
    model = model.module
    layers = model.layers
    outputs = []
    def hook(module, input, output):
        from MODELS.iterative_normalization import iterative_normalization_py
        #print(input)
        X_hat = iterative_normalization_py.apply(input[0], module.running_mean, module.running_wm, module.num_channels, module.T,
                                                 module.eps, module.momentum, module.training)
        #print(X_hat.size())
        outputs.append(X_hat.mean((0,2,3)))
    for layer in layer_list:
        layer = int(layer)
        if layer <= layers[0]:
            model.layer1[layer-1].bn1.register_forward_hook(hook)
        elif layer <= layers[0] + layers[1]:
            model.layer2[layer-layers[0]-1].bn1.register_forward_hook(hook)
        elif layer <= layers[0] + layers[1] + layers[2]:
            model.layer3[layer-layers[0]-layers[1]-1].bn1.register_forward_hook(hook)
        elif layer <= layers[0] + layers[1] + layers[2] + layers[3]:
            model.layer4[layer-layers[0]-layers[1]-layers[2]-1].bn1.register_forward_hook(hook)

    with torch.no_grad():
        model.eval()
        for X, _ in concept_loader:
            X_var = torch.autograd.Variable(X).cuda()
            model(X_var)

        mean = torch.zeros((128,)).cuda()
        for item in outputs:
            mean += item
        mean /= len(outputs)

    return mean


def rotate_cpt(concept_loader, model, mean):
    model.eval()
    model.module.change_mode(1)
    with torch.no_grad():
        for i in range(10):
            print(i)
            for X, _ in concept_loader:
                X_var = torch.autograd.Variable(X).cuda()
                model(X_var)
                R0 = model.module.layer2[0].bn1.running_rot[0,0,:]
                print(torch.dot(R0,mean)/(torch.dot(mean,mean).sqrt()))
                #print(torch.dot(R0,R0))
                #break
    model.module.change_mode(0)

def train(train_loader, concept_loader, model, criterion, optimizer, epoch):
    batch_time = AverageMeter()
    data_time = AverageMeter()
    losses = AverageMeter()
    top1 = AverageMeter()
    top5 = AverageMeter()

    # switch to train mode
    model.train()

    end = time.time()
    for i, (input, target) in enumerate(train_loader):
        if (i + 1) % 2 == 0:
            model.eval()
            model.module.change_mode(1)
            with torch.no_grad():
                for j, (X, _) in enumerate(concept_loader):
                    X_var = torch.autograd.Variable(X).cuda()
                    model(X_var)
                    break
            model.module.change_mode(0)
            model.train()
        # measure data loading time
        data_time.update(time.time() - end)
        
        target = target.cuda(async=True)
        input_var = torch.autograd.Variable(input)
        target_var = torch.autograd.Variable(target)
        
        # compute output
        output = model(input_var)
        loss = criterion(output, target_var)
        
        # measure accuracy and record loss
        prec1, prec5 = accuracy(output.data, target, topk=(1, 5))
        losses.update(loss.data, input.size(0))
        top1.update(prec1[0], input.size(0))
        top5.update(prec5[0], input.size(0))
        
        # compute gradient and do SGD step
        optimizer.zero_grad()
        loss.backward()
        optimizer.step()
        
        # measure elapsed time
        batch_time.update(time.time() - end)
        end = time.time()
        
        if i % args.print_freq == 0:
            print('Epoch: [{0}][{1}/{2}]\t'
                  'Time {batch_time.val:.3f} ({batch_time.avg:.3f})\t'
                  'Data {data_time.val:.3f} ({data_time.avg:.3f})\t'
                  'Loss {loss.val:.4f} ({loss.avg:.4f})\t'
                  'Prec@1 {top1.val:.3f} ({top1.avg:.3f})\t'
                  'Prec@5 {top5.val:.3f} ({top5.avg:.3f})'.format(
                   epoch, i, len(train_loader), batch_time=batch_time,
                   data_time=data_time, loss=losses, top1=top1, top5=top5))
  

def validate(val_loader, model, criterion, epoch):
    batch_time = AverageMeter()
    losses = AverageMeter()
    top1 = AverageMeter()
    top5 = AverageMeter()

    # switch to evaluate mode
    model.eval()
    end = time.time()
    with torch.no_grad():
        for i, (input, target) in enumerate(val_loader):
            target = target.cuda(async=True)
            input_var = torch.autograd.Variable(input, volatile=True)
            target_var = torch.autograd.Variable(target, volatile=True)
            
            # compute output
            output = model(input_var)
            loss = criterion(output, target_var)
            
            # pred = output.argmax(1).cpu().numpy()
            # if i>=2 and i<4:
            #     x = input.cpu().numpy()
            #     y = target.cpu().numpy()
            #     for j, xx in enumerate(x):
            #         img = Image.fromarray(xx[0]*255).convert('L')
            #         img.save('/usr/xtmp/zhichen/attention-module/plot/valval'+str(i)+str(j)+'_'+str(y[j])+'_'+str(pred[j])+'.png')

            # measure accuracy and record loss
            prec1, prec5 = accuracy(output.data, target, topk=(1, 5))
            losses.update(loss.data, input.size(0))
            top1.update(prec1[0], input.size(0))
            top5.update(prec5[0], input.size(0))
            
            # measure elapsed time
            batch_time.update(time.time() - end)
            end = time.time()
            
            if i % args.print_freq == 0:
                print('Test: [{0}/{1}]\t'
                    'Time {batch_time.val:.3f} ({batch_time.avg:.3f})\t'
                    'Loss {loss.val:.4f} ({loss.avg:.4f})\t'
                    'Prec@1 {top1.val:.3f} ({top1.avg:.3f})\t'
                    'Prec@5 {top5.val:.3f} ({top5.avg:.3f})'.format(
                    i, len(val_loader), batch_time=batch_time, loss=losses,
                    top1=top1, top5=top5))
        
        print(' * Prec@1 {top1.avg:.3f} Prec@5 {top5.avg:.3f}'
                .format(top1=top1, top5=top5))

    return top1.avg

def print_concept_top5(val_loader, model, whitened_layers, print_other = False):
    # switch to evaluate mode
    model.eval()
    from shutil import copyfile
    dst = '/usr/xtmp/zhichen/attention-module/plot/'
    layer_list = whitened_layers.split(',')
    folder = dst + '_'.join(layer_list) + '_rot/'
    # print(folder)
    if print_other:
        folder = dst + '_'.join(layer_list) + '_rot_otherdim/'
    if args.arch == "resnet_transfer":
        folder = dst + '_'.join(layer_list) + '_rot_transfer/'
    if not os.path.exists(folder):
        os.mkdir(folder)
    
    model = model.module
    layers = model.layers
    if args.arch == "resnet_transfer":
        model = model.model

    outputs= []
    def hook(module, input, output):
        from MODELS.iterative_normalization import iterative_normalization_py
        #print(input)
        X_hat = iterative_normalization_py.apply(input[0], module.running_mean, module.running_wm, module.num_channels, module.T,
                                                 module.eps, module.momentum, module.training)
        size_X = X_hat.size()
        size_R = module.running_rot.size()
        X_hat = X_hat.view(size_X[0], size_R[0], size_R[2], *size_X[2:])

        X_hat = torch.einsum('bgchw,gdc->bgdhw', X_hat, module.running_rot)
        #print(size_X)
        X_hat = X_hat.view(*size_X)

        outputs.append(X_hat)
    
    for layer in layer_list:
        layer = int(layer)
        if layer <= layers[0]:
            model.layer1[layer-1].bn1.register_forward_hook(hook)
        elif layer <= layers[0] + layers[1]:
            model.layer2[layer-layers[0]-1].bn1.register_forward_hook(hook)
        elif layer <= layers[0] + layers[1] + layers[2]:
            model.layer3[layer-layers[0]-layers[1]-1].bn1.register_forward_hook(hook)
        elif layer <= layers[0] + layers[1] + layers[2] + layers[3]:
            model.layer4[layer-layers[0]-layers[1]-layers[2]-1].bn1.register_forward_hook(hook)


    begin = 0
    end = 1
    if print_other:
        begin = 1
        end = 11
    with torch.no_grad():
        for k in range(begin, end):
            paths = []
            vals = None
            for i, (input, _, path) in enumerate(val_loader):
                paths += list(path)
                input_var = torch.autograd.Variable(input).cuda()
                outputs = []
                model(input_var)
                val = []
                for output in outputs:
                    val = np.concatenate((val,output.sum((2,3))[:,k].cpu().numpy()))
                val = val.reshape((len(outputs),-1))
                if i == 0:
                    vals = val
                else:
                    vals = np.concatenate((vals,val),1)

            for i, layer in enumerate(layer_list):
                arr = list(zip(list(vals[i,:]),list(paths)))
                arr.sort(key = lambda t: t[0], reverse = True)
                for j in range(50):
                    src = arr[j][1]
                    # print(src)
                    # print(folder+'layer'+layer+'_'+str(j+1)+'.jpg')
                    if print_other:
                        copyfile(src, folder+'layer'+layer+'_'+str(j+1)+'_dim'+str(k)+'.jpg')
                    else:
                        copyfile(src, folder+'layer'+layer+'_'+str(j+1)+'.jpg')
    # with torch.no_grad():
    #     arr = []
    #     for input, _, path in val_loader:
    #         input_var = torch.autograd.Variable(input).cuda()
    #         # compute output
    #         x = model.module.conv1(input_var)
    #         x = model.module.bw1(x)
    #         x = model.module.relu(x)
    #         if model.module.network_type == "ImageNet":
    #             x = model.module.maxpool(x)
    #         x = model.module.layer1[0](x)
    #         x = model.module.layer1[1].conv1(x)
    #         x = model.module.layer1[1].bn1(x)
    #         val = x.sum((2,3))[:,0].cpu().numpy()
    #         zipped = list(zip(list(val),list(path)))
    #         arr += zipped
    #     #print(arr)
    #     arr.sort(key = lambda t: t[0], reverse = True) 
        
    #     for i in range(10):
    #         print(arr[i])
    #         src = arr[i][1]
    #         copyfile(src, dst+str(i+1)+'.jpg')   

    return 0


def get_channel_attention_matrix(data, model, layer_idx, block_idx):
    resnet = model._modules['module']
    x = data

    x = resnet.conv1(x)
    x = resnet.bn1(x)
    x = resnet.relu(x)
    if resnet.network_type == "ImageNet":
        x = resnet.maxpool(x)

    for i in range(1,layer_idx):
        x = model._modules['module']._modules['layer'+str(i)](x)
    
    layer = model._modules['module']._modules['layer'+str(layer_idx)]
    for i in range(block_idx):
        x = layer[i](x)

    block = layer[layer_idx]
    x = block.conv1(x)
    x = block.bn1(x)
    x = block.relu(x)
    x = block.conv2(x)
    x = block.bn2(x)
    x = block.relu(x)
    x = block.conv3(x)
    x = block.bn3(x)

    ch_att = block.cbam.ChannelGate
    attention = ch_att.get_attention(x)
    print(attention.shape)
    return attention.cpu().data()

def save_checkpoint(state, is_best, prefix):
    #pass
    filename='./checkpoints/%s_checkpoint.pth.tar'%prefix
    torch.save(state, filename)
    if is_best:
         shutil.copyfile(filename, './checkpoints/%s_model_best.pth.tar'%prefix)


class AverageMeter(object):
    """Computes and stores the average and current value"""
    def __init__(self):
        self.reset()

    def reset(self):
        self.val = 0
        self.avg = 0
        self.sum = 0
        self.count = 0

    def update(self, val, n=1):
        self.val = val
        self.sum += val * n
        self.count += n
        self.avg = self.sum / self.count


def adjust_learning_rate(optimizer, epoch):
    """Sets the learning rate to the initial LR decayed by 10 every 30 epochs"""
    lr = args.lr * (0.1 ** (epoch // 50))
    for param_group in optimizer.param_groups:
        param_group['lr'] = lr


def accuracy(output, target, topk=(1,)):
    """Computes the precision@k for the specified values of k"""
    maxk = max(topk)
    batch_size = target.size(0)

    _, pred = output.topk(maxk, 1, True, True)
    pred = pred.t()
    correct = pred.eq(target.view(1, -1).expand_as(pred))

    res = []
    for k in topk:
        correct_k = correct[:k].view(-1).float().sum(0, keepdim=True)
        res.append(correct_k.mul_(100.0 / batch_size))
    return res


if __name__ == '__main__':
    main()
