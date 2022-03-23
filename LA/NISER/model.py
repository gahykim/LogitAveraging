#!/usr/bin/env python36
# -*- coding: utf-8 -*-
"""
Created on July, 2018

@author: Tangrizzly
"""

import datetime
import math
import numpy as np
import torch
from torch import nn
from torch.nn import Module, Parameter
import torch.nn.functional as F
import time
from utils import get_metric_scores, metric_print


class GNN(Module):
    def __init__(self, hidden_size, step=1):
        super(GNN, self).__init__()
        self.step = step
        self.hidden_size = hidden_size
        self.input_size = hidden_size * 2
        self.gate_size = 3 * hidden_size
        self.w_ih = Parameter(torch.Tensor(self.gate_size, self.input_size))
        self.w_hh = Parameter(torch.Tensor(self.gate_size, self.hidden_size))
        self.b_ih = Parameter(torch.Tensor(self.gate_size))
        self.b_hh = Parameter(torch.Tensor(self.gate_size))
        self.b_iah = Parameter(torch.Tensor(self.hidden_size))
        self.b_oah = Parameter(torch.Tensor(self.hidden_size))

        self.linear_edge_in = nn.Linear(self.hidden_size, self.hidden_size, bias=True)
        self.linear_edge_out = nn.Linear(self.hidden_size, self.hidden_size, bias=True)
        self.linear_edge_f = nn.Linear(self.hidden_size, self.hidden_size, bias=True)

    def GNNCell(self, A, hidden):
        input_in = torch.matmul(A[:, :, :A.shape[1]], self.linear_edge_in(hidden)) + self.b_iah
        input_out = torch.matmul(A[:, :, A.shape[1]: 2 * A.shape[1]], self.linear_edge_out(hidden)) + self.b_oah
        inputs = torch.cat([input_in, input_out], 2)
        gi = F.linear(inputs, self.w_ih, self.b_ih)
        gh = F.linear(hidden, self.w_hh, self.b_hh)
        i_r, i_i, i_n = gi.chunk(3, 2)
        h_r, h_i, h_n = gh.chunk(3, 2)
        resetgate = torch.sigmoid(i_r + h_r)
        inputgate = torch.sigmoid(i_i + h_i)
        newgate = torch.tanh(i_n + resetgate * h_n)
        hy = newgate + inputgate * (hidden - newgate)
        return hy

    def forward(self, A, hidden):
        for i in range(self.step):
            hidden = self.GNNCell(A, hidden)
        return hidden


class SessionGraph(Module):
    def __init__(self, opt, n_node):
        super(SessionGraph, self).__init__()
        self.hidden_size = opt.hiddenSize
        self.n_node = n_node
        self.norm = opt.norm
        self.ta = opt.TA
        self.scale = opt.scale
        self.batch_size = opt.batchSize
        self.nonhybrid = opt.nonhybrid
        self.embedding = nn.Embedding(self.n_node, self.hidden_size)
        self.gnn = GNN(self.hidden_size, step=opt.step)

        self.linear_one = nn.Linear(self.hidden_size, self.hidden_size, bias=True)
        self.linear_two = nn.Linear(self.hidden_size, self.hidden_size, bias=True)
        self.linear_three = nn.Linear(self.hidden_size, 1, bias=False)
        self.linear_transform = nn.Linear(self.hidden_size * 2, self.hidden_size, bias=True)
        
        if self.ta:
            self.linear_t = nn.Linear(self.hidden_size, self.hidden_size, bias=False)  # target attention
        self.loss_function = nn.CrossEntropyLoss()
        self.optimizer = torch.optim.Adam(self.parameters(), lr=opt.lr, weight_decay=opt.l2)
        self.scheduler = torch.optim.lr_scheduler.StepLR(self.optimizer, step_size=opt.lr_dc_step, gamma=opt.lr_dc)
        self.reset_parameters()

    def reset_parameters(self):
        stdv = 1.0 / math.sqrt(self.hidden_size)
        for weight in self.parameters():
            weight.data.uniform_(-stdv, stdv)

    def compute_scores(self, hidden, mask):
        ht = hidden[torch.arange(mask.shape[0]).long(), torch.sum(mask, 1) - 1]  # batch_size x latent_size
        q1 = self.linear_one(ht).view(ht.shape[0], 1, ht.shape[1])  # batch_size x 1 x latent_size
        q2 = self.linear_two(hidden)  # batch_size x seq_length x latent_size
        alpha = self.linear_three(torch.sigmoid(q1 + q2))
        a = torch.sum(alpha * hidden * mask.view(mask.shape[0], -1, 1).float(), 1)
        if not self.nonhybrid:
            a = self.linear_transform(torch.cat([a, ht], 1))
        
        if self.norm:
            norms = torch.norm(self.embedding.weight, p=2, dim=1).data  # l2 norm over item embedding again for b
            self.embedding.weight.data = self.embedding.weight.data.div(norms.view(-1, 1).expand_as(self.embedding.weight))
        b = self.embedding.weight[1:]  # n_nodes x latent_size
        
        if self.ta:
            qt = self.linear_t(hidden)  # batch_size x seq_length x latent_size
            beta = F.softmax(b @ qt.transpose(1, 2), -1)  # batch_size x n_nodes x seq_length
            target = beta @ hidden  # batch_size x n_nodes x latent_size
            a = a.view(ht.shape[0], 1, ht.shape[1])  # b,1,d
            a = a + target  # b,n,d
            scores = torch.sum(a * b, -1)  # b,n
        else:
            scores = torch.matmul(a, b.transpose(1, 0))
        if self.scale:
            scores = 16 * scores  # 16 is the sigma factor
        return scores

    def forward(self, inputs, A):
        if self.norm:
            norms = torch.norm(self.embedding.weight, p=2, dim=1).data  # l2 norm over item embedding
            self.embedding.weight.data = self.embedding.weight.data.div(norms.view(-1, 1).expand_as(self.embedding.weight))
        hidden = self.embedding(inputs)
        hidden = self.gnn(A, hidden)
        return hidden


def trans_to_cuda(variable):
    if torch.cuda.is_available():
        return variable.cuda()
    else:
        return variable


def trans_to_cpu(variable):
    if torch.cuda.is_available():
        return variable.cpu()
    else:
        return variable


def forward(model, i, data, top_labels):
    alias_inputs, A, items, mask, targets, top_labels_sidx = data.get_slice(i, top_labels)
    alias_inputs = trans_to_cuda(torch.Tensor(alias_inputs).long())
    items = trans_to_cuda(torch.Tensor(items).long())
    A = trans_to_cuda(torch.Tensor(A).float())
    mask = trans_to_cuda(torch.Tensor(mask).long())
    hidden = model(items, A)

    get = lambda i: hidden[i][alias_inputs[i]]
    seq_hidden = torch.stack([get(i) for i in torch.arange(len(alias_inputs)).long()])
    if model.norm:
        seq_shape = list(seq_hidden.size())
        seq_hidden = seq_hidden.view(-1, model.hidden_size)
        norms = torch.norm(seq_hidden, p=2, dim=1)  # l2 norm over session embedding
        seq_hidden = seq_hidden.div(norms.unsqueeze(-1).expand_as(seq_hidden))
        seq_hidden = seq_hidden.view(seq_shape)

    return targets, top_labels_sidx, model.compute_scores(seq_hidden, mask)


def train_test(model, train_data, test_data, n_node, top_labels, lam=1, Ks=[10, 20]):
    epoch_start_train = time.time()
    model.scheduler.step()
    print('start training: ', datetime.datetime.now())
    model.train()
    total_loss = 0.0
    slices = train_data.generate_batch(model.batch_size)
    for i, j in zip(slices, np.arange(len(slices))):
        model.optimizer.zero_grad()
        targets, top_labels_sidx, scores_o = forward(model, i, train_data, top_labels)
        targets_cuda = trans_to_cuda(torch.Tensor(targets).long())
        loss_o = model.loss_function(scores_o, targets_cuda-1)

        probs = scores_o.clone()
        with torch.no_grad():
            for i, sidx in enumerate(top_labels_sidx):
                if len(sidx) == 0:
                    pass
                else:
                    gathered_logits = torch.mean(scores_o[sidx], dim=0)
                    probs.index_copy_(0, trans_to_cuda(torch.tensor(sidx)),
                    gathered_logits.view(1, -1).repeat(len(sidx), 1))
        
        probs = trans_to_cuda(probs)
        scores_p = torch.softmax(probs, dim=1)

        loss_p = nn.functional.cross_entropy(scores_p, targets_cuda-1)
        loss = loss_o + (lam * loss_p)
        loss.backward()
        model.optimizer.step()
        total_loss += loss
        if j % 1000 == 0:
            t = time.time() - epoch_start_train
            print('[%d/%d] Loss_o: %.4f   Loss_p: %.4f   Time: %.2f' % (j, len(slices), loss_o.item(), loss_p.item(), t))
            epoch_start_train = time.time()
    
    print('\t Total Loss:\t%.3f' % total_loss)

    print('start predicting: ', datetime.datetime.now())
    epoch_start_eval = time.time()
    model.eval()
    eval10, eval20 = [[] for i in range(3)], [[] for i in range(3)]
    slices = test_data.generate_batch(model.batch_size)
    for i in slices:
        targets, _, scores = forward(model, i, test_data, top_labels)

        eval10 = get_metric_scores(scores, targets, Ks[0], eval10)
        eval20 = get_metric_scores(scores, targets, Ks[1], eval20)

    t = time.time() - epoch_start_eval
    results = metric_print(eval10, eval20, n_node, t)

    return loss, results