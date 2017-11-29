# coding: utf-8

from __future__ import unicode_literals
import numpy as np
import itertools as iter
import os
import errno
import logging
import re
import shutil
import json

"""
Tools for task representation.

These make no sense if states are not discrete (although they may be
represented as continuous vectors).
"""

from itertools import permutations

from task_models.state import State
from task_models.action import Action

# from task_models import State
# from .action import Action

main_path = "/Users/Corina/Documents/AAMAS2018/sim_data"
path_sim_train = os.path.join(main_path, "train")
path_sim_test = os.path.join(main_path, "test")
path_sim_obj = "/Users/Corina/Documents/HRT_BCT_full/GithubCode/hrteaming-bctask/" \
               "StateDiscretization/HMM/DevDebugCompleteDataSet/Features"


def check_path(path):
    """Validates a path. Raises error on invalid path.

    A valid path is a list of alternate states and actions. It must start
    and finish by a state. In all successive (s, a, s'), s must validate
    pre-condition of a and s' its post-conditions.
    Empty paths are allowed.
    """
    try:
        return (len(path) == 0 or  # Empty path
                isinstance(path[0], State) and (
                    len(path) == 1 or (  # [s] or [s, a, s, ...]
                        isinstance(path[1], Action) and
                        isinstance(path[2], State) and
                        path[1].check(path[0], path[2]) and  # pre/post
                        check_path(path[2:])
                    ))
                )
    except IndexError:
        return False


def split_path(path):
    return [(path[i], path[i + 1], path[i + 2])
            for i in range(0, len(path) - 2, 2)]


def max_cliques(graph):
    """Searches non-trivial maximum cliques in graph.

    Uses Bron-Kerbosch algorithm with pivot.

    :graph: dictionary of node: set(neighbours) representing symmetric graph
        (must verify: v in graph[u] => u in graph[v])
    :returns: iterator over cliques (as sets)
    """
    # yield from
    for clique in _bron_kerbosch(graph, set(), set(graph), set()):
        if len(clique) > 1:  # non-trivial
            yield clique


def _bron_kerbosch(graph, r, p, x):
    if len(p) == 0 and len(x) == 0:
        yield r
    elif len(p) > 0:
        pivot = max(p, key=lambda u: len(graph[u].intersection(p)))
        # (node with the most neighbours in p)
        for u in p.difference(graph[pivot]):
            # yield from...
            for clique in _bron_kerbosch(graph, r.union([u]),
                                         p.intersection(graph[u]),
                                         x.intersection(graph[u])):
                yield clique
            p.remove(u)
            x.add(u)


def unique_rows(a):
    b = a.ravel().view(np.dtype((np.void, a.dtype.itemsize * a.shape[1])))
    _, unique_idx = np.unique(b, return_index=True)
    return a[unique_idx]


def make_sure_path_exists(path):

    try:
        os.makedirs(path)
    except OSError as exception:
        if exception.errno != errno.EEXIST:
            raise


class BaseGraph(object):
    """Transitions (s, l, d) are stored as {s: {(l, d), ...}, ...}, that is
    a dictionary of sets of pairs.
    """

    def __init__(self):
        self.transitions = {}

    def __eq__(self, other):
        return (isinstance(other, BaseGraph) and
                self.transitions == other.transitions)

    def add_transition(self, source, label, destination):
        if source not in self.transitions:
            self.transitions[source] = set()
        self.transitions[source].add((label, destination))

    def has_transition(self, source, label, destination):
        return (source in self.transitions and
                (label, destination) in self.transitions[source])

    def all_transitions(self):
        for s in self.transitions:
            for (a, s_next) in self.transitions[s]:
                yield (s, a, s_next)

    def remove_transition(self, source, label, destination):
        self.transitions[source].remove((label, destination))

    def all_nodes(self):
        nodes = set()
        for s, l, d in self.all_transitions():
            nodes.add(s)
            nodes.add(d)
        return nodes

    def as_dictionary(self, name=''):
        d = {'name': name}
        all_nodes = list(enumerate(self.all_nodes()))
        d['nodes'] = [{'id': i, 'value': {'label': str(node)}}
                      for i, node in all_nodes]
        nodes_ids = dict([(n, i) for i, n in all_nodes])
        d['links'] = [{'u': nodes_ids[u],
                       'v': nodes_ids[v],
                       'value': {'label': str(l)}}
                      for u, l, v in self.all_transitions()]
        return d

    def compact(self, nodes, new_node):
        """Compact several nodes into on meta node.
        There is no difference about nodes and meta nodes, the code is agnostic
        about the nature of the node. However it replaces all label with
        an empty string, hence breaking any convention such as labels being
        states in a TaskGraph.
        """
        nodes = set(nodes)
        for (s, l, d) in list(self.all_transitions()):
            sin = s in nodes
            din = d in nodes
            if sin or din:
                self.remove_transition(s, l, d)
                if not (sin and din):  # not an inner node
                    self.add_transition(
                        new_node if sin else s, '', new_node if din else d)


class TaskGraph(BaseGraph):
    """Represents valid transitions in a task model.
    """

    def __init__(self):
        super(TaskGraph, self).__init__()
        self.initial = set()
        self.terminal = set()

    def __eq__(self, other):
        return (super(TaskGraph, self).__eq__(other) and
                isinstance(other, TaskGraph) and
                self.initial == other.initial and
                self.terminal == other.terminal)

    def add_path(self, path):
        if not check_path(path):
            raise ValueError('Invalid path.')
        if len(path) > 0:
            self.initial.add(path[0])
            self.terminal.add(path[-1])
        for (s1, a, s2) in split_path(path):
            self.add_transition(s1, a, s2)

    def has_path(self, path):
        """Checks if the path is a valid path for this task model.
        """
        if not check_path(path):
            raise ValueError('Invalid path.')
        return ((len(path) == 0) or
                (path[0] in self.initial and
                 path[-1] in self.terminal and
                 all([self.has_transition(s1, a, s2)
                      for (s1, a, s2) in split_path(path)
                      ])
                 ))

    def check_only_deterministic_transitions_from_state(self, s):
        """See get_deterministic_transitions."""
        outgoing_actions = set()
        for (a, sn) in self.transitions[s]:
            if a in outgoing_actions:
                raise ValueError(
                    "Non-deterministic transition from state {} "
                    "for action {}.".format(s, a))
            outgoing_actions.add(a)

    def check_only_deterministic_transitions(self):
        """Deterministic means: for each (s, a) there is at most one s' such
        as (s, a, s') is a transition.
        Raises ValueError if there are non-deterministic transitions.
        """
        for s in self.transitions:
            self.check_only_deterministic_transitions_from_state(s)

    def conjugate(self):
        return ConjugateTaskGraph.from_task_graph(self)


class AbstractAction(Action):
    def __hash__(self):
        return hash(self.name)

    def __eq__(self, other):
        return isinstance(other, AbstractAction) and self.name == other.name

    def check(self, pre, post):
        return True

    def copy(self, rename_format='{}'):
        return AbstractAction(rename_format.format(self.name))


class PredAction(AbstractAction):
    """Action abstraction to be used for the prediction project.

    :param name: str (must be unique)
    :param durations: (float: human, float: robot, float: error)
        Duration for human and robot to perform the action as well as error
        time when robot starts action at wrong moment (the error time includes
        the time of acting before interruption as well as the recovery time).
    :param human_probability: float
        Probability that the human would take care of this action. If not
        defined, will have to be estimated.
    :param fail_probability: float
        Probability that the robot action fails.
    :param no_probability: float
        Probability that the human answers no to the robot asking if he can
        take the action.
    """

    def __init__(self, name, num_feats, feat_probs):
        super(PredAction, self).__init__(name=name)
        self.num_feats = num_feats
        self.feat_probs = feat_probs

        @property
        def feat_probs(self):
            return self.__feat_probs

        @feat_probs.setter
        def feat_probs(self, feat_probs):
            if self.num_feats != len(feat_probs):
                raise ValueError("num_feats != len(feat_probs). "
                                 "Should have prob values for each feature.")
            else:
                self.__feat_probs = feat_probs

    def copy(self, rename_format='{}'):
        return PredAction(rename_format.
                          format(self.name), self.num_feats, self.feat_probs)


class ConjugateTaskGraph(BaseGraph):
    initial = AbstractAction('initial')
    terminal = AbstractAction('terminal')

    @classmethod
    def from_task_graph(cls, tg):
        ctg = cls()
        tg.check_only_deterministic_transitions()
        # Add transitions of the form: initial --(s_init)--> a
        for s in tg.initial:
            for (a, _) in tg.transitions[s]:
                ctg.add_transition(ctg.initial, s, a)
        for outgoings in tg.transitions.values():  # Whatever origin state
            for (a, next_state) in outgoings:
                if next_state in tg.terminal:
                    # Add transitions of the form: a --(s_term)--> terminal
                    ctg.add_transition(a, next_state, ctg.terminal)
                else:
                    # Look ahead...
                    for (next_action, _) in tg.transitions[next_state]:
                        # ...and add transitions of the form: a --(s')--> a'
                        ctg.add_transition(a, next_state, next_action)
        return ctg

    def compact(self, nodes, new_node):
        # TODO: is this really the desired behavior?
        super(ConjugateTaskGraph, self).compact(nodes, new_node)
        if self.initial in nodes:
            self.initial = new_node
        if self.terminal in nodes:
            self.terminal = new_node

    def get_max_chains(self, exclude=[]):
        """Search non-trivial maximum chains in the graph.

        :returns: iterator over chains (as lists)
        """
        in_degree = {}
        unique_transitions = {n: set() for n in self.all_nodes()}
        for (s, l, d) in self.all_transitions():
            if d not in unique_transitions[s]:
                in_degree[d] = in_degree.get(d, 0) + 1
            unique_transitions[s].add(d)
        to_explore = set([self.initial])
        explored = set()
        chain = None

        def explore_successors(node):
            to_explore.update(unique_transitions[node].difference(explored))

        while len(to_explore) > 0 or chain is not None:
            if chain is None:
                node = to_explore.pop()
                explored.add(node)
                if len(unique_transitions[node]) == 1:  # out degree == 1
                    chain = [node]
                else:
                    explore_successors(node)
            else:
                node = chain[-1]
                assert (len(unique_transitions[node]) == 1)
                next_node = list(unique_transitions[node])[0]
                done = False
                if in_degree[next_node] == 1:
                    chain.append(next_node)
                    explored.add(next_node)
                    if len(unique_transitions[next_node]) != 1:
                        done = True
                        explore_successors(next_node)
                else:
                    to_explore.add(next_node)
                    done = True
                if done:
                    if len(chain) > 1:
                        yield chain
                    chain = None

    def get_max_cliques(self):
        """Searches non-trivial maximum cliques in the symmetrized graph.

        :returns: iterator over cliques (as sets)
        """
        # compute symmetric graph
        unique = set((s, d) for (s, _, d) in self.all_transitions())
        symmetric = {n: set() for n in self.all_nodes()}
        for s, d in unique:
            if (d, s) in unique:
                symmetric[s].add(d)
                symmetric[d].add(s)
        return max_cliques(symmetric)


# Hierarchical task definition

class MetaAction(AbstractAction):
    SEP = {'sequence': '→',
           'parallel': '||',
           'alternative': '∨',
           }

    def __init__(self, kind, actions):
        super(MetaAction, self).__init__(self._name(kind, actions))
        self.kind = kind
        self.actions = actions

    def __eq__(self, other):
        return (isinstance(other, MetaAction) and
                self.kind == other.kind and
                self.actions == other.actions)

    def __hash__(self):
        return hash(self.name)
        # Needs to re-implement __hash__ despite inheritence (see following
        # from datamodels doc).
        # A class that overrides __eq__() and does not define __hash__() will
        # have its __hash__() implicitly set to None. When the __hash__()
        # method of a class is None, instances of the class will raise an
        # appropriate TypeError when a program attempts to retrieve their hash
        # value, and will also be correctly identified as unhashable when
        # checking isinstance(obj, collections.Hashable).

    def to_combination(self):
        children = [
            a.to_combination() if isinstance(a, MetaAction) else LeafCombination(a)
            for a in self.actions]
        return COMBINATION_CLASSES[self.kind](children)

    @classmethod
    def _name(cls, kind, actions):
        return '({})'.format(cls.SEP[kind].join([a.name for a in actions]))


def int_generator():
    i = -1
    while True:
        i += 1
        yield i


class Combination(object):
    kind = 'Undefined'

    def __init__(self, children, name='unnamed', highlighted=False,
                 probabilities=None):
        self.children = children  # Actions or combinations
        self.name = name
        self.highlighted = highlighted
        self.proba = probabilities

    @property
    def proba(self):
        return self.__proba

    @proba.setter
    def proba(self, probabilities):
        num_children = len(self.children)
        if len(probabilities) != num_children:
            raise ValueError("Length of probabilities array should be equal \
                             to number of children of combination.")
        else:
            self.__proba = probabilities

    def _meta_dictionary(self, parent_id, id_generator):
        attr = []
        if self.highlighted:
            attr.append('highlighted')
        return {'name': self.name,
                'id': next(id_generator),
                'parent': parent_id,
                'combination': self.kind,
                'attributes': attr,
                }

    def as_dictionary(self, parent_id, id_generator):
        d = self._meta_dictionary(parent_id, id_generator)
        d['children'] = [
            c.as_dictionary(d['id'], id_generator)
            for c in self.children
        ]
        return d

    def _deep_copy_children(self, rename_format='{}'):
        return [c.deep_copy(rename_format=rename_format)
                for c in self.children]


class LeafCombination(Combination):
    kind = None

    def __init__(self, action, highlighted=False):
        self.highlighted = highlighted
        self.action = action

    @property
    def name(self):
        return self.action.name

    @property
    def children(self):
        raise ValueError('A leaf does not have children.')

    def as_dictionary(self, parent, id_generator):
        return self._meta_dictionary(parent, id_generator)

    def deep_copy(self, rename_format='{}'):
        return LeafCombination(self.action.copy(rename_format=rename_format),
                               highlighted=self.highlighted)


class SequentialCombination(Combination):
    kind = 'Sequence'

    def __init__(self, children, **xargs):
        super(SequentialCombination, self).__init__(children, **xargs)

    @property
    def proba(self):
        return super(SequentialCombination, self).proba
        # return self.__proba

    @proba.setter
    def proba(self, probabilities):
        correct = False
        num_children = len(self.children)
        if probabilities is None:
            correct = True
        elif all(prob == 1 for prob in probabilities):
            correct = True
        if correct is True:
            return super(SequentialCombination, self.__class__). \
                proba.fset(self, [1] * num_children)
        else:
            raise ValueError("Sequential combinations have default probs \
                             set to [1] * num children.")

    def deep_copy(self, rename_format='{}'):
        return SequentialCombination(
            self._deep_copy_children(rename_format=rename_format),
            probabilities=self.proba,
            name=rename_format.format(self.name),
            highlighted=self.highlighted)


class AlternativeCombination(Combination):
    kind = 'Alternative'

    def __init__(self, children, **xargs):
        super(AlternativeCombination, self).__init__(children, **xargs)

    @property
    def proba(self):
        return super(AlternativeCombination, self).proba

    @proba.setter
    def proba(self, probabilities):
        if probabilities is None:
            num_children = len(self.children)
            prob = float(1) / num_children
            return super(AlternativeCombination, self.__class__). \
                proba.fset(self, [prob] * num_children)
        elif np.min(probabilities) < 0:
            raise ValueError("At least one prob value is < 0.")
        elif np.max(probabilities) > 1:
            raise ValueError("At least one prob value is > 1.")
        elif not (np.isclose(np.sum(probabilities), 1)):
            raise ValueError("Probs should sum to 1.")
        else:
            return super(AlternativeCombination, self.__class__). \
                proba.fset(self, probabilities)

    def deep_copy(self, rename_format='{}'):
        return AlternativeCombination(
            self._deep_copy_children(rename_format=rename_format),
            probabilities=self.proba,
            name=rename_format.format(self.name),
            highlighted=self.highlighted)


class ParallelCombination(Combination):
    kind = 'Parallel'

    def __init__(self, children, **xargs):
        super(ParallelCombination, self).__init__(children, **xargs)

    @property
    def proba(self):
        return super(ParallelCombination, self).proba

    @proba.setter
    def proba(self, probabilities):
        correct = False
        if probabilities is None:
            correct = True
        elif all(prob is None for prob in probabilities):
            correct = True
        if correct is True:
            num_children = len(self.children)
            return super(ParallelCombination, self.__class__). \
                proba.fset(self, [None] * num_children)
        else:
            raise ValueError("Parallel combinations shouldn't have \
            probs specified.")

    def deep_copy(self, rename_format='{}'):
        return ParallelCombination(
            self._deep_copy_children(rename_format=rename_format),
            probabilities=self.proba,
            name=rename_format.format(self.name),
            highlighted=self.highlighted)

    def to_alternative(self):
        sequences = [
            SequentialCombination(
                [c.deep_copy('{{}} order-{}'.format(i)) for c in p],
                name='{} order-{}'.format(self.name, i))
            for i, p in enumerate(permutations(self.children))
        ]
        return AlternativeCombination(sequences, name=self.name,
                                      highlighted=self.highlighted)


class TrajectoryElement(object):
    """Objects out of which we build trajectories.
    Trajectories are sequences of leaves generated from an HTM.
    """

    def __init__(self, node, prob):
        self.node = node
        self.prob = prob

    @property
    def prob(self):
        return self.__prob

    @prob.setter
    def prob(self, prob):
        if prob < 0:
            raise ValueError("Prob < 0.")
        elif prob > 1:
            raise ValueError("Prob > 1.")
        else:
            self.__prob = prob


class HierarchicalTask(object):
    """Tree representing a hierarchy of tasks which leaves are actions."""

    def __init__(self, root=None, name=None, num_feats_action=None,
                 share_feats=False,
                 num_supp_bhvs=None, num_supp_bhvs_named=None, num_supp_bhvs_rd=None,
                 supp_bhvs_dict=dict(), supp_bhvs_named_dict=dict(), supp_bhvs_rd_dict=dict()):
        self.root = root
        self.name = name
        self.all_trajectories = []
        self.bin_trajectories = []
        self.bin_trajectories_test = []
        self.obj_names = dict()
        self.obj_ints = dict()
        self.feat_names = dict()
        self.feat_ints = dict()
        self.num_obj = 0
        self.share_feats = share_feats
        self.num_feats_action = num_feats_action
        self.train_set_actions = []
        self.train_set_actions_traj = []
        self.test_set_actions = []
        self.test_set_actions_traj = []
        self.train_set_sb = []
        self.train_set_sb_actions = []
        self.train_set_sb_traj = []
        self.train_set_sb_blind_robot = []
        self.train_set_sb_actions_blind_robot = []
        self.train_set_sb_traj_blind_robot = []
        self.test_set_sb = []
        self.test_set_sb_traj = []
        self.test_set_sb_actions = []
        self.train_set_sb_named = []
        self.train_set_sb_actions_named = []
        self.train_set_sb_traj_named = []
        self.train_set_sb_blind_robot_named = []
        self.train_set_sb_actions_blind_robot_named = []
        self.train_set_sb_traj_blind_robot_named = []
        self.test_set_sb_named = []
        self.test_set_sb_traj_named = []
        self.test_set_sb_actions_named = []
        self.train_set_sb_rd = []
        self.train_set_sb_actions_rd = []
        self.train_set_sb_traj_rd = []
        self.train_set_sb_blind_robot_rd = []
        self.train_set_sb_actions_blind_robot_rd = []
        self.train_set_sb_traj_blind_robot_rd = []
        self.test_set_sb_rd = []
        self.test_set_sb_traj_rd = []
        self.test_set_sb_actions_rd = []
        self.train_set = []
        self.num_supp_bhvs = num_supp_bhvs
        self.user_prefs_dict = dict()
        self.supp_bhvs_dict = supp_bhvs_dict
        self.supp_bhvs_dict_rev = dict()
        self.num_supp_bhvs_named = num_supp_bhvs_named
        self.user_prefs_named_dict = dict()
        self.supp_bhvs_named_dict = supp_bhvs_named_dict
        self.supp_bhvs_named_dict_rev = dict()
        self.num_supp_bhvs_rd = num_supp_bhvs_rd
        self.user_prefs_rd_dict = dict()
        self.supp_bhvs_rd_dict = supp_bhvs_rd_dict
        self.supp_bhvs_rd_dict_rev = dict()
        self.gen_dict()

    def is_empty(self):
        return self.root is None

    def as_dictionary(self, name=None):
        return {
            'name': 'Hierarchical task tree' if name is None else name,
            'nodes': None if self.is_empty() else self.root.as_dictionary(
                None, int_generator()),
        }

    @property
    def user_prefs_dict(self):
        return self.__user_prefs_dict

    @user_prefs_dict.setter
    def user_prefs_dict(self, user_prefs_dict):
        self.__user_prefs_dict = user_prefs_dict

    def gen_dict(self):
        regexp = r'( order)'
        self._gen_dict_rec(self.root, regexp)
        self.obj_ints = dict(map(reversed, self.obj_names.items()))
        if self.share_feats is True:
            for feat_idx in range(self.num_feats_action):
                self.feat_names["feat_" + str(feat_idx)] = feat_idx
        else:
            self.feat_names = self.obj_names
        self.feat_ints = dict(map(reversed, self.feat_names.items()))
        self.supp_bhvs_dict_rev = dict(map(reversed, self.supp_bhvs_dict.items()))
        self.supp_bhvs_named_dict_rev = dict(map(reversed, self.supp_bhvs_named_dict.items()))
        self.supp_bhvs_rd_dict_rev = dict(map(reversed, self.supp_bhvs_rd_dict.items()))

    def _gen_dict_rec(self, node, regexp):
        if isinstance(node, LeafCombination):
            name = node.name
            rem = re.search(regexp, node.name)
            if rem:
                name = node.name[:rem.start()]
            if not(name in self.obj_names.keys()):
                self.obj_names[name] = self.num_obj
                self.num_obj += 1
        else:
            for child in node.children:
                self._gen_dict_rec(child, regexp)

    def base_name(self, name):
        regexp = r'( order)'
        rem = re.search(regexp, name)
        if rem:
            name = name[:rem.start()]
        return name

    def gen_all_trajectories(self):
        self.all_trajectories = \
            self._gen_trajectories_rec(self.root)

    def _gen_trajectories_rec(self, node):
        """Generates all possible trajectories from an HTM.

        :returns: list of tuples of the following form
        [(proba, [LC1, LC2, ...]), (), ...]
        Each tuple represents a trajectory of the HTM,
        with the first element equal to the probability of that trajectory
        and the second element equal to the list of leaf combinations.
        """
        if isinstance(node, LeafCombination):
            return [(1, [node])]
        elif isinstance(node, ParallelCombination):
            return self._gen_trajectories_rec(node.to_alternative())
        elif isinstance(node, AlternativeCombination):
            children_trajectories = [(p * node.proba[c_idx], seq)
                                     for c_idx, c in enumerate(node.children)
                                     for p, seq in self._gen_trajectories_rec(c)]
            return children_trajectories
        elif isinstance(node, SequentialCombination):
            children_trajectories = [
                self._gen_trajectories_rec(c)
                for c_idx, c in enumerate(node.children)]
            new_trajectories = []
            product_trajectories = list(
                iter.product(*children_trajectories))
            for product in product_trajectories:
                probas, seqs = zip(*product)
                new_trajectories.append((float(np.product(probas)),
                                         list(iter.chain.from_iterable(seqs))))
            return new_trajectories
        else:
            raise ValueError("Reached invalid type during recursion.")

    def write_task_dict_to_file(self, path, task_name):
        make_sure_path_exists(path)
        f = os.path.join(path_sim_obj, "task_{}_obj.txt".format(task_name))
        # if os.path.isfile(f) is False:
        f_out = open(f, 'w')
        if self.share_feats is False:
            sorted_vals = sorted(self.obj_names, key=self.obj_names.get)
            for el in sorted_vals:
                # f_out.write("{:>20} {}\n".format(el, obj_names[el]))
                f_out.write("{} {}\n".format(self.obj_names[el], el))
        else:
            for feat_idx in range(self.num_feats_action):
                f_out.write("{} {}\n".format(feat_idx, "feat_"+str(feat_idx)))
        f_out.close()
        # else:
        #     logging.warning("Did not save file task_{}_obj.txt "
        #                     "because it already exists. "
        #                     .format(task_name))

    def write_feats_to_file(self, path, task_name):
        make_sure_path_exists(path)
        f = os.path.join(path_sim_obj, "task_{}_obj.txt".format(task_name))

    def write_traj_to_file(self, f_out, bin_feats, task_name, traj_idx):
        # if os.path.isfile(f_out) is False:
        np.savetxt(f_out, bin_feats, fmt=str("%d"))
        # else:
        #     logging.warning("Did not save file task_{}_traj_{}.bfout "
        #                     "because it already exists. "
        #                     "Continuing onto the next trajectory."
        #                     .format(task_name, traj_idx))

    def gen_bin_feats_test_set(self, path_train, path_test, task_name, test_set_size):
        for i in range(test_set_size):
            self.bin_trajectories_test.append(self.bin_trajectories[i])
            shutil.copy2(os.path.join(path_train, "task_{}_traj_{}.bfout".
                                      format(task_name, i)), path_test)
        self.bin_trajectories_test = np.array(self.bin_trajectories_test)

    def gen_bin_feats_traj(self, cum_feats=False):
        """Generates binary features for all possible trajectories from an HTM
        and writes them to file.
        Should only be called after calling gen_all_trajectories().
        """
        if self.all_trajectories:
            regexp = r'( order)'
            trajectories = list(zip(*self.all_trajectories))[1]
            bin_feats_init = np.array([0] * len(self.obj_names))
            task_name = "jdoe"
            if self.name:
                task_name = self.name
            path = os.path.join(path_sim_train, task_name)
            self.write_task_dict_to_file(path, task_name)
            for traj_idx, traj in enumerate(trajectories):
                bin_feats = np.tile(bin_feats_init,
                                    (len(set(traj)) + 1, 1))
                for node_idx, node in enumerate(traj):
                    if cum_feats is True:
                        keys = [self.obj_names.get(key.name) if re.search(regexp, key.name) is None
                                else self.obj_names.get(key.name[:re.search(regexp, key.name).start()])
                                for key in traj[:node_idx + 1]]
                        bin_feats[node_idx + 1, keys] = 1
                    else:
                        action = self.obj_names.get(traj[node_idx].name)
                        if action is None:
                            action = self.obj_names.get(traj[node_idx]
                                                        .name[:re.search(regexp, traj[node_idx].name).start()])
                        bin_feats[node_idx + 1, action] = 1
                #bin_feats = unique_rows(bin_feats)
                f_out = os.path.join(path, "task_{}_traj_{}.bfout".
                                        format(task_name, traj_idx))
                self.bin_trajectories.append(bin_feats)
                self.write_traj_to_file(f_out, bin_feats, task_name, traj_idx)
            self.bin_trajectories = np.array(self.bin_trajectories)
            path_test = os.path.join(path_sim_test, task_name)
            make_sure_path_exists(path_test)
            self.gen_bin_feats_test_set(path, path_test, task_name, 6)
            # for i in range(6):
            #     shutil.copy2(os.path.join(path, "task_{}_traj_{}.bfout".format(task_name, i)), path_test)
        else:
            raise ValueError("Cannot generate bin feats before generating all trajectories.")

    def gen_object_bin_feats_sb(self, num_feats, feat_probs):
        """Generates binary features only for an object from the HTM.

        :param num_feats: number of features this object has (for now, same for all objects)
        :param feat_probs: list of probabilities of length = num_feats, where each prob
        is used to generate each of the binary features for this object (for now, each prob
        is generated using a uniform distribution)

        :returns: list of num_feats binary features generated based on the feat_probs list
        """
        if num_feats != len(feat_probs):
            raise ValueError("num_feats != len(feat_probs)"
                             "You should pass in prob values for all features.")
        bin_feats_obj = np.random.binomial(1, feat_probs, size=num_feats)
        return bin_feats_obj

    def gen_training_set_actions(self, size, bias=False):
        if len(self.all_trajectories) == 0:
            self.gen_all_trajectories()
        self.train_set_actions, self.train_set_actions_traj = \
            self._gen_set_bin_feats_actions(self.all_trajectories, size, path_sim_train, bias=bias)

    def gen_test_set_actions(self, size):
        if len(self.train_set_actions) == 0:
            raise ValueError("There exists no training set. Cannot create test set.")
        self.test_set_actions, self.test_set_actions_traj = \
            self._gen_set_bin_feats_actions(self.all_trajectories, size, path_sim_test)

    def _gen_set_bin_feats_actions(self, trajectories, size, path_sim_local,
                                   bias=False, bias_weight=50):
        """Generates sets to be used for a dataset only for actions.
        It excludes supportive behaviors."""
        len_traj = len(trajectories)
        set_bin_feats = []
        regexp = r'( order)'

        task_name = "jdoe"
        if self.name:
            task_name = self.name
        path = os.path.join(path_sim_local, task_name)
        self.write_task_dict_to_file(path, task_name)

        if self.share_feats is True:
            traj_bin_feats_init = np.array([0] * self.num_feats_action)
        else:
            traj_bin_feats_init = np.array([0] * (self.num_obj*self.num_feats_action))
        set_actions_traj_local = []
        bias_progress = 0
        for i in range(size):
            traj = trajectories[np.random.randint(0, len_traj)][1]
            if bias is True and bias_progress <= bias_weight:
                zero_leaf_name = self.base_name(traj[0].name)
                fourth_leaf_name = self.base_name(traj[4].name)
                while zero_leaf_name != 'gatherparts_leg_3' \
                        or fourth_leaf_name != 'gatherparts_leg_1':
                    traj = trajectories[np.random.randint(0, len_traj)][1]
                    zero_leaf_name = self.base_name(traj[0].name)
                    fourth_leaf_name = self.base_name(traj[4].name)
                bias_progress += 1
            set_actions_traj_local.append(traj)
            traj_bin_feats = np.tile(traj_bin_feats_init,
                                (len(set(traj)) + 1, 1))
            for node_idx, node in enumerate(traj):
                node_bin_feats_sb = self.gen_object_bin_feats_sb(
                    node.action.num_feats, node.action.feat_probs)
                if self.share_feats is True:
                    traj_bin_feats[node_idx + 1, :] = node_bin_feats_sb
                else:
                    action = self.obj_names.get(traj[node_idx].name)
                    if action is None:
                        action = self.obj_names.get(traj[node_idx].name[:re.
                                                    search(regexp, traj[node_idx].name).start()])
                    start_idx = action * self.num_feats_action
                    end_idx = start_idx + self.num_feats_action
                    traj_bin_feats[node_idx + 1, start_idx:end_idx] = node_bin_feats_sb
            # traj_bin_feats = \
            #     [self.gen_object_bin_feats_sb(leaf.action.num_feats, leaf.action.feat_probs)
            #      for leaf in traj]
            #traj_bin_feats.insert(0, np.array([0] * self.num_feats_action))
            f_out = os.path.join(path, "task_{}_traj_{}.bfout".
                                 format(task_name, i))
            self.write_traj_to_file(f_out, traj_bin_feats, task_name, i)
            set_bin_feats.append(traj_bin_feats)
        return np.array(set_bin_feats), set_actions_traj_local

    def gen_train_set_sb_blind_robot(self, user_prefs, test_set_size):
        self.train_set_sb_blind_robot, \
        self.train_set_sb_traj_blind_robot, \
        self.train_set_sb_actions_blind_robot = \
            self._gen_set_sb(self.train_set_actions_traj, self.train_set_actions,
                             user_prefs, test_set_size, sb_type='prob')

    def gen_train_set_sb_blind_robot_named(self, user_prefs, test_set_size):
        self.train_set_sb_blind_robot_named, \
        self.train_set_sb_traj_blind_robot_named, \
        self.train_set_sb_actions_blind_robot_named = \
            self._gen_set_sb(self.train_set_actions_traj, self.train_set_actions,
                             user_prefs, test_set_size, sb_type='named')

    def gen_train_set_sb_blind_robot_rd(self, user_prefs, test_set_size):
        self.train_set_sb_blind_robot_rd, \
        self.train_set_sb_traj_blind_robot_rd, \
        self.train_set_sb_actions_blind_robot_rd = \
            self._gen_set_sb(self.train_set_actions_traj, self.train_set_actions,
                             user_prefs, test_set_size, sb_type='rd')

    def gen_train_set_sb(self, user_prefs, num_dems):
        self.train_set_sb, self.train_set_sb_traj, self.train_set_sb_actions = \
            self._gen_set_sb(self.train_set_actions_traj, self.train_set_actions,
                             user_prefs, num_dems, sb_type='prob')

    def gen_test_set_sb(self, user_prefs, num_dems):
        self.test_set_sb, self.test_set_sb_traj, self.test_set_sb_actions = \
            self._gen_set_sb(self.test_set_actions_traj, self.test_set_actions, user_prefs,
                             num_dems, sb_type='prob')

    def gen_train_set_sb_named(self, user_prefs, num_dems):
        self.train_set_sb_named, self.train_set_sb_traj_named, self.train_set_sb_actions_named = \
            self._gen_set_sb(self.train_set_actions_traj, self.train_set_actions,
                             user_prefs, num_dems, sb_type='named')

    def gen_test_set_sb_named(self, user_prefs, num_dems):
        self.test_set_sb_named, self.test_set_sb_traj_named, self.test_set_sb_actions_named = \
            self._gen_set_sb(self.test_set_actions_traj, self.test_set_actions,
                             user_prefs, num_dems, sb_type='named')

    def gen_train_set_sb_rd(self, user_prefs, num_dems):
        self.train_set_sb_rd, self.train_set_sb_traj_rd, self.train_set_sb_actions_rd = \
            self._gen_set_sb(self.train_set_actions_traj, self.train_set_actions,
                             user_prefs, num_dems, sb_type='rd')

    def gen_test_set_sb_rd(self, user_prefs, num_dems):
        self.test_set_sb_rd, self.test_set_sb_traj_rd, self.test_set_sb_actions_rd = \
            self._gen_set_sb(self.test_set_actions_traj, self.test_set_actions,
                             user_prefs, num_dems, sb_type='rd')

    def _gen_set_sb(self, trajectories, traj_actions, users_prefs, num_dems, sb_type='rd'):
        len_train_traj = len(trajectories)
        if len_train_traj == 0:
            raise ValueError("Please generate training set w/ actions "
                             "before generating training supportive behavior set.")
        set_sb = []
        set_sb_traj = []
        set_sb_actions = []
        for user in sorted(users_prefs.keys()):
            if sb_type == 'prob':
                all_dems_user_bin_feats, user_sb_traj, user_sb_actions = \
                    self._gen_user_sb(trajectories, traj_actions, users_prefs[user], num_dems)
            elif sb_type == 'named':
                all_dems_user_bin_feats, user_sb_traj, user_sb_actions = \
                    self._gen_user_sb_named(trajectories, traj_actions, users_prefs[user], num_dems)
            elif sb_type == 'rd':
                all_dems_user_bin_feats, user_sb_traj, user_sb_actions = \
                    self._gen_user_sb_rd(trajectories, traj_actions, users_prefs[user], num_dems)
            set_sb.append(all_dems_user_bin_feats)
            set_sb_traj.append(user_sb_traj)
            set_sb_actions.append(user_sb_actions)
        return np.array(set_sb), np.array(set_sb_traj), np.array(set_sb_actions)

    def _gen_user_sb(self, trajectories, traj_actions, user_prefs, num_dems):
        len_train_traj = len(trajectories)
        all_dems_user_bin_feats = []
        regexp = r'( order)'
        user_sb_traj = []
        user_sb_actions = []
        for i in range(num_dems):
            traj_idx = np.random.randint(0, len_train_traj)
            traj = trajectories[traj_idx]
            user_sb_traj.append(trajectories[traj_idx])
            user_sb_actions.append(traj_actions[traj_idx])
            traj_user_bin_feats = []
            for leaf in traj:
                name = leaf.name
                rem = re.search(regexp, leaf.name)
                if rem:
                    name = leaf.name[:rem.start()]
                traj_user_bin_feats.append(user_prefs[name])
            traj_user_bin_feats.insert(0, np.array([0] * self.num_supp_bhvs))
            all_dems_user_bin_feats.append(traj_user_bin_feats)
        return all_dems_user_bin_feats, user_sb_traj, user_sb_actions

    def _gen_user_sb_named(self, trajectories, traj_actions, user_prefs, num_dems):
        len_train_traj = len(trajectories)
        len_traj = traj_actions.shape[1]
        all_dems_user_bin_feats = []
        regexp = r'( order)'
        user_sb_traj = []
        user_sb_actions = []
        for i in range(num_dems):
            traj_idx = np.random.randint(0, len_train_traj)
            traj = trajectories[traj_idx]
            user_sb_traj.append(trajectories[traj_idx])
            user_sb_actions.append(traj_actions[traj_idx])
            traj_user_bin_feats = np.zeros((len_traj, self.num_supp_bhvs_named))
            num_leg_leaves = 0
            for leaf_idx, leaf in enumerate(traj):
                name = leaf.name
                rem = re.search(regexp, leaf.name)
                if rem:
                    name = leaf.name[:rem.start()]
                if name.startswith('bring_leg'):
                    num_leg_leaves += 1
                for key in user_prefs.keys():
                    if (name in user_prefs[key]) \
                        or ('end' in user_prefs[key] and leaf_idx == len_traj-2)  \
                        or ('all_bring_leg' in user_prefs[key] and name.startswith('bring_leg')) \
                        or (sum(['time_leg' in leg for leg in user_prefs[key]]) >= 1
                            and int(user_prefs[key][0].split('_')[2]) == num_leg_leaves):
                        traj_user_bin_feats[leaf_idx+1, self.supp_bhvs_named_dict[key]] = 1
            all_dems_user_bin_feats.append(traj_user_bin_feats)
        return all_dems_user_bin_feats, user_sb_traj, user_sb_actions

    def _gen_user_sb_rd(self, trajectories, traj_actions, user_prefs, num_dems):
        len_train_traj = len(trajectories)
        len_traj = traj_actions.shape[1]
        all_dems_user_bin_feats = []
        regexp = r'( order)'
        user_sb_traj = []
        user_sb_actions = []
        for i in range(num_dems):
            traj_idx = np.random.randint(0, len_train_traj)
            traj = trajectories[traj_idx]
            user_sb_traj.append(trajectories[traj_idx])
            user_sb_actions.append(traj_actions[traj_idx])
            traj_user_bin_feats = np.zeros((len_traj, self.num_supp_bhvs_rd))
            num_gp_leg_leaves = 0
            num_gp_front_leg_leaves = 0
            num_gp_back_leg_leaves = 0
            num_ass_leg_leaves = 0
            num_ass_front_leg_leaves = 0
            num_ass_back_leg_leaves = 0
            first_time = True
            for leaf_idx, leaf in enumerate(traj):
                name = leaf.name
                rem = re.search(regexp, leaf.name)
                if rem:
                    name = leaf.name[:rem.start()]
                name_comp = name.split('_')
                if name.startswith('gatherparts_leg'):
                    num_gp_leg_leaves += 1
                    if name_comp[2] in ['1', '2']:
                        num_gp_front_leg_leaves += 1
                    elif name_comp[2] in ['3', '4']:
                        num_gp_back_leg_leaves += 1
                elif name.startswith('assemble_leg'):
                    num_ass_leg_leaves += 1
                    if name_comp[2] in ['1', '2']:
                        num_ass_front_leg_leaves += 1
                    elif name_comp[2] in ['3', '4']:
                        num_ass_back_leg_leaves += 1
                for key in user_prefs.keys():
                    change = False
                    if sum(['time_assemble_leg' in leg for leg in user_prefs[key]]) >= 1 \
                            and int(user_prefs[key][0].split('_')[3]) == num_ass_leg_leaves \
                            and first_time is True:
                        change = True
                        first_time = False
                    if (name in user_prefs[key]) \
                        or ('end' in user_prefs[key] and leaf_idx == len_traj-2)  \
                        or ('all_gatherparts_leg' in user_prefs[key] and name.startswith('gatherparts_leg')) \
                        or (change is True) \
                        or (sum(['all_gatherparts_leg' in leg for leg in user_prefs[key]]) >= 1
                            and name.startswith('gatherpart_leg')) \
                        or (sum(['all_assemble_leg' in leg for leg in user_prefs[key]]) >= 1
                            and name.startswith('assemble_leg')):
                        traj_user_bin_feats[leaf_idx+1, self.supp_bhvs_rd_dict[key]] = 1
            all_dems_user_bin_feats.append(traj_user_bin_feats)
        return all_dems_user_bin_feats, user_sb_traj, user_sb_actions

    def reset_sb_sets(self):
        self.train_set_sb = []
        self.train_set_sb_actions = []
        self.train_set_sb_traj = []
        self.test_set_sb = []
        self.test_set_sb_traj = []
        self.test_set_sb_actions = []

    def reset_sb_sets_named(self):
        self.train_set_sb_named = []
        self.train_set_sb_actions_named = []
        self.train_set_sb_traj_named = []
        self.test_set_sb_named = []
        self.test_set_sb_traj_named = []
        self.test_set_sb_actions_named = []

    def reset_sb_sets_rd(self):
        self.train_set_sb_rd = []
        self.train_set_sb_actions_rd = []
        self.train_set_sb_traj_rd = []
        self.test_set_sb_rd = []
        self.test_set_sb_traj_rd = []
        self.test_set_sb_actions_rd = []

    def reset_sb_sets_blind_robot(self):
        self.train_set_sb_blind_robot = []
        self.train_set_sb_actions_blind_robot = []
        self.train_set_sb_traj_blind_robot = []
        self.train_set_sb_blind_robot_named = []
        self.train_set_sb_actions_blind_robot_named = []
        self.train_set_sb_traj_blind_robot_named = []
        self.train_set_sb_blind_robot_rd = []
        self.train_set_sb_actions_blind_robot_rd = []
        self.train_set_sb_traj_blind_robot_rd = []

    def reset_main_sets(self):
        self.all_trajectories = []
        self.bin_trajectories = []
        self.bin_trajectories_test = []
        self.train_set_actions = []
        self.train_set_actions_traj = []
        self.test_set_actions = []
        self.test_set_actions_traj = []


def debugging():
    ## Define the task
    mount_central = SequentialCombination([
        LeafCombination(PredAction(
            'Get central frame', (1, 0, 0, 0, 0, 0, 0, 0, 0))),
        LeafCombination(PredAction(
            'Start Hold central frame', (1, 1, 0, 0, 0, 0, 0, 0, 0)))],
        name='Mount central frame')
    mount_legs = ParallelCombination([
        SequentialCombination([
            LeafCombination(PredAction(
                'Get left leg', (1, 1, 1, 0, 0, 0, 0, 0, 0))),
            LeafCombination(PredAction(
                'Snap left leg', (1, 1, 1, 1, 0, 0, 0, 0, 0))),
        ], name='Mount left leg'),
        SequentialCombination([
            LeafCombination(PredAction(
                'Get right leg', (1, 1, 1, 1, 1, 0, 0, 0, 0))),
            LeafCombination(PredAction(
                'Snap right leg', (1, 1, 1, 1, 1, 1, 0, 0, 0))),
        ], name='Mount right leg'),
    ],
        name='Mount legs')
    release_central = LeafCombination(
        PredAction('Release central frame', (1, 1, 1, 1, 1, 1, 1, 0, 0)))
    mount_top = SequentialCombination([
        LeafCombination(PredAction('Get top', (1, 1, 1, 1, 1, 1, 1, 1, 0))),
        LeafCombination(PredAction('Snap top', (1, 1, 1, 1, 1, 1, 1, 1, 1)))],
        name='Mount top')

    # mount_central_alt = mount_central.to_alternative()
    # mount_legs_alt = mount_legs.to_alternative()
    # print(mount_central_alt.children)
    # print(mount_legs_alt.children)

    mount_legs_alt = mount_legs.to_alternative()

    chair_task_root = SequentialCombination(
        [mount_central, mount_legs, release_central, mount_top], name='Mount chair')

    test_parallel = ParallelCombination([mount_top, release_central])
    test_seq = SequentialCombination([mount_top, release_central])

    chair_task = HierarchicalTask(root=chair_task_root)
    chair_task_parallel = HierarchicalTask(root=test_parallel)
    chair_task_seq = HierarchicalTask(root=test_seq)

    chair_task_dict = chair_task.as_dictionary()
    # print("yay")
    # print(chair_task_dict)

    test = 1

    leaf = LeafCombination(PredAction(
        'l', (1, 0, 0, 0, 0, 0, 0, 0, 0)))
    simple_task_leaf = HierarchicalTask(root=leaf)

    a = LeafCombination(PredAction(
        'a', (1, 0, 0, 0, 0, 0, 0, 0, 0)))
    a1 = LeafCombination(PredAction(
        'a1', (1, 0, 0, 0, 0, 0, 0, 0, 0)))
    a2 = LeafCombination(PredAction(
        'a2', (1, 0, 0, 0, 0, 0, 0, 0, 0)))
    a11 = LeafCombination(PredAction(
        'a11', (1, 0, 0, 0, 0, 0, 0, 0, 0)))
    b = LeafCombination(PredAction(
        'b', (1, 0, 0, 0, 0, 0, 0, 0, 0)))
    c = LeafCombination(PredAction(
        'c', (1, 0, 0, 0, 0, 0, 0, 0, 0)))
    ab = SequentialCombination([a, b])
    ba = SequentialCombination([b, a])
    a1a2 = SequentialCombination([a1, a2])
    a1a2a11 = ParallelCombination([a1a2, a11])
    alt_aux1 = AlternativeCombination([ab, ba], probabilities=[0.8, 0.2])
    alt_aux2 = AlternativeCombination([ab, ba], probabilities=[0.1, 0.9])

    alt_a = LeafCombination(AbstractAction('a'))
    alt_b = LeafCombination(AbstractAction('b'))
    alt_c = LeafCombination(AbstractAction('c'))
    alt_p1 = ParallelCombination([alt_a, alt_b])
    alt_p2 = ParallelCombination([alt_p1, alt_c])
    alt_alt = alt_p2.to_alternative()
    alt_probs = AlternativeCombination([a, b, c])

    simple_task_parallel = HierarchicalTask(root=ParallelCombination([a, b, c]))
    simple_task_parallel2 = HierarchicalTask(root=ParallelCombination([a, b]))
    simple_task_seq = HierarchicalTask(root=SequentialCombination([a, b, c]))
    simple_task_alt = HierarchicalTask(root=AlternativeCombination([a, b, c]))
    two_level_task1 = HierarchicalTask(root=ParallelCombination([ab, c]), name="two_level_task1")
    two_level_task2 = HierarchicalTask(root=AlternativeCombination([SequentialCombination([a, b]), c]))
    two_level_task3 = HierarchicalTask(root=SequentialCombination([SequentialCombination([a, b]), c]))
    two_level_task4 = HierarchicalTask(root=AlternativeCombination([c, SequentialCombination([a, b])]))
    two_level_task5 = HierarchicalTask(root=ParallelCombination([ParallelCombination([a, b]), c]))
    two_level_task6 = HierarchicalTask(root=ParallelCombination([c, ParallelCombination([a, b])]))
    three_level_task1 = HierarchicalTask(root=ParallelCombination([SequentialCombination([a1a2, b]), c]))
    three_level_task2 = HierarchicalTask(root=SequentialCombination([SequentialCombination([a1a2, b]), c]))
    four_level_task1 = HierarchicalTask(root=SequentialCombination([SequentialCombination([a1a2a11, b]), c]))
    custom_task1 = HierarchicalTask(root=AlternativeCombination(
        [SequentialCombination([alt_aux1, c]), SequentialCombination([c, alt_aux2])], probabilities=[0.7, 0.3]))

    b_l1 = LeafCombination(AbstractAction('bring_leg1'))
    b_l2 = LeafCombination(AbstractAction('bring_leg2'))
    b_l3 = LeafCombination(AbstractAction('bring_leg3'))
    b_l4 = LeafCombination(AbstractAction('bring_leg4'))
    b_s = LeafCombination(AbstractAction('bring_seat'))
    b_b = LeafCombination(AbstractAction('bring_back'))
    b_scr = LeafCombination(AbstractAction('bring_screwdriver'))
    a_legs_1 = ParallelCombination([b_l1, b_l2, b_l3, b_l4], name='attach_legs')
    a_rest_1 = ParallelCombination([b_s, b_b], name='attach_rest')
    a_l1_2 = ParallelCombination([b_l1, b_scr], name='attach_leg1')
    a_l2_2 = ParallelCombination([b_l2, b_scr], name='attach_leg2')
    a_l3_2 = ParallelCombination([b_l3, b_scr], name='attach_leg3')
    a_l4_2 = ParallelCombination([b_l4, b_scr], name='attach_leg4')
    a_s_2 = ParallelCombination([b_s, b_scr], name='attach_seat')
    a_b_2 = ParallelCombination([b_b, b_scr], name='attach_back')
    a_legs_2 = ParallelCombination([a_l1_2, a_l2_2, a_l3_2, a_l4_2], name='attach_legs')
    a_rest_2 = ParallelCombination([a_s_2, a_b_2], name='attach_rest')

    sim_task1 = HierarchicalTask(root=SequentialCombination([b_scr, a_legs_1, a_rest_1], name='complete'),
                                 name='sim_task1')
    sim_task2 = HierarchicalTask(root=SequentialCombination([a_legs_2, a_rest_2], name='complete'),
                                 name='sim_task2')
    sim_task3 = HierarchicalTask(root=ParallelCombination([b_l1, b_l2], name='complete'), name='sim_task3')
    sim_task_action1 = HierarchicalTask(root=SequentialCombination([b_scr, a_legs_1, a_rest_1], name='complete'),
                                 name='sim_task_action1')

    # chair_task.gen_all_trajectories()
    # chair_task_parallel.gen_all_trajectories()
    # chair_task_seq.gen_all_trajectories()
    simple_task_leaf.gen_all_trajectories()
    simple_task_parallel2.gen_all_trajectories()
    simple_task_parallel.gen_all_trajectories()
    simple_task_seq.gen_all_trajectories()
    simple_task_alt.gen_all_trajectories()
    two_level_task1.gen_all_trajectories()
    two_level_task2.gen_all_trajectories()
    two_level_task3.gen_all_trajectories()
    two_level_task4.gen_all_trajectories()
    two_level_task5.gen_all_trajectories()
    two_level_task6.gen_all_trajectories()
    three_level_task1.gen_all_trajectories()
    three_level_task2.gen_all_trajectories()
    four_level_task1.gen_all_trajectories()
    custom_task1.gen_all_trajectories()
    sim_task1.gen_all_trajectories()
    sim_task2.gen_all_trajectories()
    sim_task3.gen_all_trajectories()
    sim_task_action1.gen_all_trajectories()
    print("---")
    # print("Length of generated trajectories ", len(chair_task.all_trajectories))
    # for traj in chair_task.all_trajectories:
    #    print(traj.leaf.name)

    #simple_task_seq.gen_bin_feats_traj()
    #two_level_task1.gen_bin_feats_traj()

    #sim_task1.gen_bin_feats_traj()
    #sim_task2.gen_bin_feats_traj()
    sim_task3.gen_bin_feats_traj()
    sim_task_action1.gen_bin_feats_traj()
    print("---")


def main():
    debugging()


if __name__ == '__main__':
    main()

COMBINATION_CLASSES = {'sequence': SequentialCombination,
                       'parallel': ParallelCombination,
                       'alternative': AlternativeCombination,
                       }
