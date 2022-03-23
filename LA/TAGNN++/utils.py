import pdb

import networkx as nx
import numpy as np
from collections import Counter
import pickle
import os
import random

def top75_labels(train_data, test_data, dataset_name):
    try:
        with open(f'../../Dataset/{dataset_name}/top75_labels.pickle', 'rb') as f:
            top_labels = pickle.load(f)
    except:
        labels = train_data[1] + test_data[1]

        target_cnt_dict = Counter(labels)
        target_dict_sorted = sorted(target_cnt_dict.items(), reverse=True, key=lambda item: item[1])
        target_dict_keys = [item[0] for item in target_dict_sorted]
        target_dict_values = [item[1] for item in target_dict_sorted]

        occr_cum_sum = np.cumsum(np.array(target_dict_values))
        split_point = int(round(len(labels) * 0.75))
        split_idx = np.sum(occr_cum_sum < split_point)

        top_labels = target_dict_keys[:split_idx]

        with open(f'../../Dataset/{dataset_name}/top75_labels.pickle', 'wb') as f:
            pickle.dump(top_labels, f, pickle.HIGHEST_PROTOCOL)

    return top_labels


def get_metric_scores(scores, targets, k, eval):
    # eval : hit, mrr, cov, arp, tail, tailcov
    sub_scores = scores.topk(k)[1]
    sub_scores = sub_scores.cpu().detach().numpy()

    cur_hits, cur_mrrs = [], []
    for score, target in zip(sub_scores, targets):

        # hit@K
        hit_temp = np.isin(target - 1, score)
        cur_hits.append(hit_temp)

        # MRR@K
        if len(np.where(score == target - 1)[0]) == 0:
            mrr_temp = 0
        else:
            mrr_temp = 1 / (np.where(score == target - 1)[0][0] + 1)
        cur_mrrs.append(mrr_temp)


    eval[0] += cur_hits
    eval[1] += cur_mrrs


    # Coverage@K
    eval[2] += np.unique(sub_scores).tolist()

    return eval


def metric_print(eval10, eval20, n_node, time):

    for evals in [eval10, eval20]:
        # hit, mrr, cov, arp, tail, tailcov
        evals[0] = np.mean(evals[0]) * 100
        evals[1] = np.mean(evals[1]) * 100
        evals[2] = len(np.unique(evals[2])) / n_node * 100

    print('Metric\t\tHR@10\tMRR@10\tCov@10')
    print(f'Value\t\t' + '\t'.join(format(eval, ".2f") for eval in eval10))

    print('Metric\t\tHR@20\tMRR@20\tCov@20')
    print(f'Value\t\t' + '\t'.join(format(eval, ".2f") for eval in eval20))

    print(f"Time elapse : {time}")

    return [eval10, eval20]


def get_best_result(results, epoch, best_results, best_epochs):
    # results: eval10, eval20
    # eval: hit, mrr, cov, arp, tail, tailcov

    for result, best_result, best_epoch in zip(results, best_results, best_epochs):
        flag = 0
        for i in range(3):
            if result[i] > best_result[i]:
                best_result[i] = result[i]
                best_epoch[i] = epoch
                flag = 1

    print("-" * 100)
    print('Best Result\tHR@10\tMRR@10\tCov@10\tEpochs')
    print(f'Value\t\t' + '\t'.join(format(result, ".2f") for result in best_results[0])
          + '\t\t' + ', '.join(str(epoch) for epoch in best_epochs[0]))

    print('Best Result\tHR@20\tMRR@20\tCov@20\tEpochs')
    print(f'Value\t\t' + '\t'.join(format(result, ".2f") for result in best_results[1])
          + '\t\t' + ', '.join(str(epoch) for epoch in best_epochs[1]))

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


class Data():
    def __init__(self, data,  shuffle=False, graph=None):
        inputs = data[0]
        inputs, mask, len_max = data_masks(inputs, [0])
        self.inputs = np.asarray(inputs)
        self.mask = np.asarray(mask)
        self.len_max = len_max
        self.targets = np.asarray(data[1])
        self.length = len(inputs)
        self.shuffle = shuffle
        self.graph = graph


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

    def get_slice(self, i, top_labels):
        inputs, mask, targets = self.inputs[i], self.mask[i], self.targets[i]
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

        top_labels_sidx = []
        for label in top_labels:
            try:
                sidx = np.where(targets == label)[0]
                top_labels_sidx.append(sidx.tolist())
            except:
                top_labels_sidx.append([])

        return alias_inputs, A, items, mask, targets, top_labels_sidx
