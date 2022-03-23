#!/usr/bin/env python36
# -*- coding: utf-8 -*-
"""
Created on July, 2018

@author: Tangrizzly
"""

import networkx as nx
import numpy as np
import random
import itertools

def get_metric_scores(scores, targets, k, eval):
    # eval : hit, mrr, cov
    sub_scores = scores.topk(k)[1]
    sub_scores = sub_scores.cpu().detach().numpy()
    
    cur_hits, cur_mrrs = [], []
    for score, target in zip(sub_scores, targets):
        # hit@K
        hit_temp = np.isin(target-1, score)
        cur_hits.append(hit_temp)
        
        # MRR@K
        if len(np.where(score == target-1)[0]) == 0:
            mrr_temp = 0
        else:
            mrr_temp = 1 / (np.where(score == target-1)[0][0] + 1)
        cur_mrrs.append(mrr_temp)
    
    eval[0] += cur_hits
    eval[1] += cur_mrrs
    # Coverage@K
    eval[2] += np.unique(sub_scores).tolist()

    return eval

def metric_print(eval10, eval20, n_node, time):

    for evals in [eval10, eval20]:
        # hit, mrr, cov
        evals[0] = np.mean(evals[0]) * 100
        evals[1] = np.mean(evals[1]) * 100
        evals[2] = len(np.unique(evals[2])) / n_node * 100
        
    print('Metric\t\tHR@10\tMRR@10\tCov@10\tHR@20\tMRR@20\tCov@20')
    print(f'Value\t\t'+'\t'.join(format(eval, ".2f") for eval in eval10+eval20))

    print(f"Time elapse : {time}")
    return [eval10, eval20]


def get_best_result(results, epoch, best_results, best_epochs):
    # results: eval10, eval20
    # eval: HR, MRR, cov

    for result, best_result, best_epoch in zip(results, best_results, best_epochs):
        flag = 0
        for i in range(3):           
            if result[i] > best_result[i]:
                best_result[i] = result[i]
                best_epoch[i] = epoch
                flag += 1

    print("-"*100)
    print('Best Result\tHR@10\tMRR@10\tCov@10\tHR@20\tMRR@20\tCov@20\tEpochs')
    print(f'Value\t\t' + '\t'.join(format(result, ".2f") for result in best_results[0]+best_results[1])
          + '\t' + ', '.join(str(epoch) for epoch in best_epochs[0]+best_epochs[1]))

    return flag


def build_graph(train_data):
    graph = nx.DiGraph()
    for seq in train_data:
        for i in range(len(seq) - 1):
            if graph.get_edge_data(seq[i], seq[i + 1]) is None:
                weight = 1
            else:
                weight = graph.get_edge_data(seq[i], seq[i + 1])['weight'] + 1
            graph.add_edge(seq[i], seq[i + 1], weight=weight)
    for node in graph.nodes:
        sum = 0
        for j, i in graph.in_edges(node):
            sum += graph.get_edge_data(j, i)['weight']
        if sum != 0:
            for j, i in graph.in_edges(i):
                graph.add_edge(j, i, weight=graph.get_edge_data(j, i)['weight'] / sum)
    return graph


def data_masks(all_usr_pois, item_tail):
    us_lens = [len(upois) for upois in all_usr_pois]
    len_max = max(us_lens)
    us_pois = [upois + item_tail * (len_max - le) for upois, le in zip(all_usr_pois, us_lens)]
    us_msks = [[1] * le + [0] * (len_max - le) for le in us_lens]
    return us_pois, us_msks, len_max


def split_validation(train_set, valid_portion):
    train_set_x, train_set_y = train_set
    n_samples = len(train_set_x)
    sidx = np.arange(n_samples, dtype='int32')
    np.random.shuffle(sidx)
    n_train = int(np.round(n_samples * (1. - valid_portion)))
    valid_set_x = [train_set_x[s] for s in sidx[n_train:]]
    valid_set_y = [train_set_y[s] for s in sidx[n_train:]]
    train_set_x = [train_set_x[s] for s in sidx[:n_train]]
    train_set_y = [train_set_y[s] for s in sidx[:n_train]]

    return (train_set_x, train_set_y), (valid_set_x, valid_set_y)


def random_deletion(sessions, targets):
    candidate_sess_idx = [i for i, sess in enumerate(sessions) if len(sess) > 1]
    aug_sess_sidx = random.sample(candidate_sess_idx, int(len(candidate_sess_idx)*0.8))

    aug_sess = []
    aug_tars = []
    for i in aug_sess_sidx:
        cur_sess = sessions[i].copy()
        remove_item = random.choice(cur_sess)
        cur_sess.remove(remove_item)
        aug_sess.append(cur_sess)
        aug_tars.append(targets[i])

    return aug_sess, aug_tars

def random_insertion(sessions, targets, len_max):
    candidate_sess_idx = [i for i, sess in enumerate(sessions) if len(sess) < len_max]
    aug_sess_sidx = random.sample(candidate_sess_idx, int(len(candidate_sess_idx)*0.8))
    candidate_item = list(set(itertools.chain.from_iterable(sessions)))
    
    aug_sess = []
    aug_tars = []
    for i in aug_sess_sidx:
        cur_sess = sessions[i].copy()
        insert_index = random.randint(0, len(cur_sess)-1)
        cur_sess.insert(insert_index, random.choice(candidate_item))
        aug_sess.append(cur_sess)
        aug_tars.append(targets[i])

    return aug_sess, aug_tars

def create_aug_sessions(batch_seqs, targets, input_aug_type, len_max, sample_num=100):

    if input_aug_type == 'deletion':
        aug_sess, aug_tars = random_deletion(batch_seqs, targets)
    elif input_aug_type == 'insertion':
        aug_sess, aug_tars = random_insertion(batch_seqs, targets, len_max)
            
    lens = [len(sess) for sess in aug_sess]
    aug_sess_pois = [sess + [0]* (len_max - le) for sess, le in zip(aug_sess, lens)]
    aug_msks = [[1] * le + [0] * (len_max-le) for le in lens]

    return np.array(aug_sess_pois), np.array(aug_msks), np.array(aug_tars)


class Data():
    def __init__(self, data, input_aug_type=None, shuffle=False, graph=None):
        inputs = data[0]
        inputs, mask, len_max = data_masks(inputs, [0])
        self.inputs = np.asarray(inputs)
        self.mask = np.asarray(mask)
        self.len_max = len_max
        self.targets = np.asarray(data[1])
        self.length = len(inputs)
        self.shuffle = shuffle
        self.graph = graph
        self.input_aug_type = input_aug_type

    def generate_batch(self, batch_size):
        if self.shuffle:
            shuffled_arg = np.arange(self.length)
            np.random.shuffle(shuffled_arg)
            self.inputs = self.inputs[shuffled_arg]
            self.mask = self.mask[shuffled_arg]
            self.targets = self.targets[shuffled_arg]
        n_batch = int(self.length / batch_size)
        if self.length % batch_size != 0:
            n_batch += 1
        slices = np.split(np.arange(n_batch * batch_size), n_batch)
        slices[-1] = slices[-1][:(self.length - batch_size * (n_batch - 1))]
        return slices

    def get_slice(self, i):
        inputs, mask, targets = self.inputs[i], self.mask[i], self.targets[i]

        if self.input_aug_type is not None:
            batch_seqs = []
            for i in range(inputs.shape[0]):
                nonzero_edges = np.stack(np.nonzero(inputs)).T
                edges = nonzero_edges[nonzero_edges[:, 0] == i]
                seq = [inputs[i, j].tolist() for i, j in edges] + [targets[i]]
                batch_seqs.append(seq)
            # pdb.set_trace()
            aug_inputs, aug_masks, aug_targets = create_aug_sessions(batch_seqs, targets, self.input_aug_type, self.len_max)
            inputs = np.concatenate([inputs, aug_inputs], axis=0)
            mask = np.concatenate([mask, aug_masks], axis=0)
            targets = np.concatenate([targets, aug_targets], axis=0)


        items, n_node, A, alias_inputs = [], [], [], []
        for u_input in inputs:
            n_node.append(len(np.unique(u_input)))
        max_n_node = np.max(n_node)
        for u_input in inputs:
            node = np.unique(u_input)
            items.append(node.tolist() + (max_n_node - len(node)) * [0])
            u_A = np.zeros((max_n_node, max_n_node))
            for i in np.arange(len(u_input) - 1):
                if u_input[i + 1] == 0:
                    break
                u = np.where(node == u_input[i])[0][0]
                v = np.where(node == u_input[i + 1])[0][0]
                u_A[u][v] = 1
            u_sum_in = np.sum(u_A, 0)
            u_sum_in[np.where(u_sum_in == 0)] = 1
            u_A_in = np.divide(u_A, u_sum_in)
            u_sum_out = np.sum(u_A, 1)
            u_sum_out[np.where(u_sum_out == 0)] = 1
            u_A_out = np.divide(u_A.transpose(), u_sum_out)
            u_A = np.concatenate([u_A_in, u_A_out]).transpose()
            A.append(u_A)
            alias_inputs.append([np.where(node == i)[0][0] for i in u_input])
        
        return alias_inputs, np.array(A), items, mask, targets
