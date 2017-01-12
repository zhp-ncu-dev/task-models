# encoding: utf-8

import os
import math
import json
import subprocess
from distutils import spawn

import numpy as np

from .py23 import TemporaryDirectory, Queue


SOLVER_NAME = 'pomdp-solve'


class ValueFunctionParseError(ValueError):
    pass


class Impossible(ValueError):
    pass


def parse_value_function(reader):
    has_action = False
    actions = []
    vectors = []
    for line in reader:
        if not line.isspace():
            if has_action:
                # expect vector
                vectors.append(np.fromstring(line, sep=' '))
                has_action = False
            else:
                # expect action
                actions.append(int(line))
                has_action = True
        # else: skip line
    if has_action:
        raise ValueFunctionParseError('Action defined but no vectors follows.')
    return actions, np.vstack(vectors)


def parse_policy_graph(reader):
    actions = []
    transitions = []
    for i, line in enumerate(reader):
        line = line.rstrip('\n').rstrip()
        if not line.isspace():
            # 'N A  Z1 Z2 Z3'.split(' ') -> ['N', 'A', '', 'Z1', 'Z2', 'Z3']
            l = line.split(' ')
            n = int(l[0])  # Node name
            assert(n == i)
            actions.append(int(l[1]))
            transitions.append([None if x == '-' else int(x) for x in l[3:]])
    return actions, transitions


PREAMBLE_FMT = """discount: {discount}
values: reward
states: {states}
actions: {actions}
observations: {observations}
"""
DECIMALS = 5
NUMBER_FORMAT = '{:0.' + str(DECIMALS) + 'f}'


def _as_list(lst_or_int):
    if isinstance(lst_or_int, int):
        return list(range(lst_or_int))
    else:
        return lst_or_int


def _dump_list(lst):
    return ' '.join([str(x) for x in lst])


def _dump_list_or_count(lst_or_int):
    if isinstance(lst_or_int, int):
        return str(lst_or_int)
    else:
        return _dump_list(lst_or_int)


def _dump_1d_array(a):
    # Make sure that sum stays the same even after trunc
    trunc_sum = np.around(a.sum(), decimals=DECIMALS)
    trunc = np.around(a, decimals=DECIMALS)
    imax = np.argmax(trunc)
    # Compensate on max to avoid negative values
    trunc[imax] += trunc_sum - trunc.sum()
    return ' '.join([NUMBER_FORMAT.format(x) for x in trunc])


def _dump_2d_array(a):
    return '\n'.join([_dump_1d_array(x) for x in a])


def _dump_3d_array(a, name, xs):
    """Dump a 3d array for a POMDP file.

    :param a: the array
    :param name: the name of the array in the file
    :param xs: names of the first dimension
    """
    return '\n'.join([
        "{} : {}\n{}".format(name, x, _dump_2d_array(a[ix, :, :]))
        for ix, x in enumerate(xs)
        ])


def _dump_4d_array(a, name, xs, ys):
    """Dump a 4d array for a POMDP file.

    :param a: the array
    :param name: the name of the array in the file
    :param xs: names of the first dimension
    :param ys: names of the second dimension
    """
    return '\n'.join([
        _dump_3d_array(a[ix, :, :, :], name,
                       ["{} : {}".format(x, y) for y in ys])
        for ix, x in enumerate(xs)
        ])


def _assert_normal(array, name):
    message = "Probabilities in {} should sum to 1."
    if not np.allclose(array.sum(-1), 1.):
        raise ValueError(message.format(name))


class POMDP:

    """Partially observable Markov model.

    :param T: array of shape (n_actions, n_states, n_states)
        Transition probabilities (must sum to 1 on last dimension)
    :param O: array of shape (n_actions, n_states, n_observations)
        Observation probabilities (action, *end state*, observation)
        (must sum to 1 on last dimension)
    :param R: array of shape (n_actions, n_states, n_states, n_observations)
        Rewards or cost (must sum to 1 on last dimension)
    :param start: array os shape (n_states)
        Initial state probabilities
    :param discount: discount factor (int)
    :param states: None | iterable of states
        Default to range(n_states).
    :param actions: None | iterable of actions
        Default to range(n_actions).
    :param observations: None | iterable of observations
        Default to range(n_observations).
    :values: ('reward' | 'cost')
        How to interpret reward coefficients.
    :solver_path: string
        Path in which to look for the executable (default to $PATH)
    """

    def __init__(self, T, O, R, start, discount, states=None, actions=None,
                 observations=None, values='reward', solver_path=None):
        # Defaults for actions, states and observations
        a, s, o = O.shape
        self._init_states(states, s)
        self._init_actions(actions, a)
        self._init_observations(observations, o)
        self.T = T
        self.O = O
        if values == 'reward':
            self.R = R
        elif values == 'cost':
            self.R = -R
        else:
            raise ValueError(
                "Values must be 'reward' of 'cost. Got '{}'.".format(values))
        self.start = start
        self._assert_shapes()
        self._assert_normal()
        self._assert_unique()
        if discount > 1 or discount < 0:
            raise ValueError('Discount factor must be ≤ 1 and ≥ 0.')
        self.discount = discount
        self._solver_path = spawn.find_executable(SOLVER_NAME,
                                                  path=solver_path)
        if self._solver_path is None:
            raise ImportError('Could not find executable for pomdp-solve.')

    def _init_states(self, states, s):
        if states is not None:
            self._s = list(states)
        else:
            self._s = s

    def _init_actions(self, actions, a):
        if actions is not None:
            self._a = list(actions)
        else:
            self._a = a

    def _init_observations(self, observations, o):
        if observations is not None:
            self._o = list(observations)
        else:
            self._o = o

    @property
    def states(self):
        return _as_list(self._s)

    @property
    def actions(self):
        return _as_list(self._a)

    @property
    def observations(self):
        return _as_list(self._o)

    @property
    def n_states(self):
        return len(self.states)

    @property
    def n_actions(self):
        return len(self.actions)

    @property
    def n_observations(self):
        return len(self.observations)

    def _assert_shapes(self):
        s = self.n_states
        a = self.n_actions
        o = self.n_observations
        message = "Wrong shape for {}: got {}, expected {}."

        def assert_shape(array, name, shape):
            if array.shape != shape:
                raise ValueError(message.format(name, array.shape, shape))

        assert_shape(self.start, 'start', (s,))
        assert_shape(self.T, 'T', (a, s, s))
        assert_shape(self.O, 'O', (a, s, o))
        assert_shape(self.R, 'R', (a, s, s, o))

    def _assert_normal(self):
        _assert_normal(self.start, 'start')
        _assert_normal(self.T, 'T')
        _assert_normal(self.O, 'O')

    def _assert_unique(self):
        message = "Found duplicate {}: {}"

        def assert_no_dup(lst, name):
            if not len(set(lst)) == len(lst):
                dup = list(lst)
                for a in set(lst):
                    dup.remove(a)
                raise ValueError(message.format(name, dup))

        assert_no_dup(self.states, 'states(s)')
        assert_no_dup(self.actions, 'action(s)')
        assert_no_dup(self.observations, 'observation(s)')

    def belief_update(self, a, o, b):
        new_b = b.dot(self.T[a, ...]) * self.O[a, :, o]
        s = new_b.sum()
        if s == 0.:
            raise Impossible('Impossible observation: ' + str(o))
        return new_b / new_b.sum()

    def sample_transition(self, a, s):
        new_s = np.random.choice(self.n_states, p=self.T[a, s, :])
        o = np.random.choice(self.n_observations, p=self.O[a, new_s, :])
        r = self.R[a, s, new_s, o]
        return new_s, o, r

    def dump(self):
        """Write POMDP description following:
        `<http://www.pomdp.org/code/pomdp-file-spec.html>`_
        """
        preamble = PREAMBLE_FMT.format(
            discount=self.discount,
            states=_dump_list_or_count(self._s),
            actions=_dump_list_or_count(self._a),
            observations=_dump_list_or_count(self._o))
        start = "start: {}".format(_dump_1d_array(np.asarray(self.start)))
        T = _dump_3d_array(self.T, 'T', self.actions)
        O = _dump_3d_array(self.O, 'O', self.actions)
        R = _dump_4d_array(self.R, 'R', self.actions, self.states)
        return '\n\n'.join([preamble, start, T, O, R])

    def dump_to(self, path, name):
        full_path = os.path.join(path, name + '.pomdp')
        with open(full_path, 'w') as f:
            f.write(self.dump())
        return full_path

    def to_dict(self):
        return {'T': self.T.tolist(),
                'O': self.O.tolist(),
                'R': self.R.tolist(),
                'start': self.start.tolist(),
                'discount': self.discount,
                'states': self.states,
                'actions': self.actions,
                'observations': self.observations,
                }

    def as_json(self):
        return json.dumps(self.to_dict())

    def save_as_json(self, path):
        with open(path, 'w') as f:
            json.dump(self.to_dict(), f)

    @classmethod
    def from_dict(cls, d):
        return cls(np.asarray(d['T']), np.asarray(d['O']), np.asarray(d['R']),
                   np.asarray(d['start']), d['discount'], states=d['states'],
                   actions=d['actions'], observations=d['observations'],
                   values='reward')

    @classmethod
    def from_json(cls, s):
        d = json.loads(s)
        return cls.from_dict(d)

    @classmethod
    def load_from_json(cls, path):
        with open(path) as f:
            d = json.load(f)
            return cls.from_dict(d)

    def randomize(self, p_unexpected=1.e-3):
        self.T += p_unexpected
        self.T /= self.T.sum(-1)[..., None]
        self.O += p_unexpected
        self.O /= self.O.sum(-1)[..., None]

    def solve(self, timeout=None, n_iterations=None, method='incprune',
              grid_type=None, seed=None, verbose=False):
        """
        :param method: incprune | grid (incprune)
        :param grid_type: simplex | pairwise (simplex)
        """
        name = 'tosolve'
        args = []
        if timeout is not None:
            args.extend(['-time_limit', str(timeout)])
        if n_iterations is not None:
            args.extend(['-horizon', str(n_iterations)])
        if seed is None:
            seed = np.random.randint(1.e10)
        args.extend(['-rand_seed', str(seed)])
        if method == 'grid':
            if grid_type is None:
                grid_type = 'simplex'
            args.extend(['-method', method, '-fg_type', grid_type])
        with TemporaryDirectory() as tmpdir:
            pomdp_file = self.dump_to(tmpdir, name)
            args.extend(['-o', name, '-pomdp', pomdp_file])
            with open(os.devnull, 'w') as DEVNULL:
                subprocess.check_call(
                    [self._solver_path] + args, cwd=tmpdir,
                    stdout=None if verbose else DEVNULL)
            return self.load_policy_from(tmpdir, name)

    def load_policy_from(self, path, name):
        value_function_file = os.path.join(path, name + '.alpha')
        policy_graph_file = os.path.join(path, name + '.pg')
        with open(value_function_file, 'r') as vf:
            actions, vf = parse_value_function(vf)
        with open(policy_graph_file, 'r') as pf:
            actions2, pg = parse_policy_graph(pf)
        assert(actions == actions2)
        assert(max(actions) < len(self.actions))
        assert(max([t for ts in pg for t in ts if t is not None]) <= len(pg))
        action_names = [self.actions[a] for a in actions]
        return GraphPolicy(action_names, self.observations, pg, vf,
                           start=self.start)


class GraphPolicy:

    def __init__(self, actions, observations, transitions, values, start=None,
                 init=None):
        self.actions = actions
        self.observations = observations
        self.transitions = np.asarray(transitions)
        assert(self.transitions.shape == (self.n_nodes, len(observations)))
        self.values = np.asarray(values)
        if init is not None:
            assert(init < self.n_nodes)
            self.init = init
        elif start is not None:
            self.init = self.get_node_from_belief(start)
        else:
            raise ValueError('Must specify either init node or start belief.')

    @property
    def n_nodes(self):
        return len(self.actions)

    def get_node_from_belief(self, b):
        return self.values.dot(b[:, np.newaxis]).argmax()

    def get_action(self, current):
        return self.actions[current]

    def next(self, current, observation):
        return self.transitions[current, self.observations.index(observation)]

    def to_dict(self):
        return {'actions': self.actions,
                'observations': self.observations,
                'transitions': self.transitions.tolist(),
                'values': self.values.tolist(),
                'initial': str(self.init),
                }

    def to_json(self, indent=None):
        return json.dumps(self.to_dict(), indent=indent)

    def save_as_json(self, path, indent=None):
        with open(path, 'w') as fp:
            json.dump(self.to_dict(), fp, indent=indent)

    @classmethod
    def from_dict(cls, d):
        return cls(d['actions'], d['observations'], d['transitions'],
                   d['values'], init=int(d['initial']))

    @classmethod
    def from_json(cls, s):
        return cls.from_dict(json.loads(s))

    @classmethod
    def load_from_json(cls, path):
        with open(path) as f:
            return cls.from_dict(json.load(f))


class GraphPolicyRunner(object):

    def __init__(self, graph_policy):
        self.gp = graph_policy
        self.reset()

    def reset(self, belief=None):
        if belief is not None:
            self.current = self.gp.get_node_from_belief(belief)
        else:
            self.current = self.gp.init

    def get_action(self):
        return self.gp.get_action(self.current)

    def step(self, observation):
        self.current = self.gp.next(self.current, observation)
        if self.current is None:
            raise Impossible('Got unexpected observation')


class GraphPolicyBeliefRunner(GraphPolicyRunner):

    def __init__(self, graph_policy, pomdp):
        self.gp = graph_policy
        self.pomdp = pomdp
        self.reset()

    def reset(self, belief=None):
        if belief is None:
            belief = self.pomdp.start
        self.current_belief = belief
        super(GraphPolicyBeliefRunner, self).reset(belief=belief)

    def step(self, observation):
        a = self.pomdp.actions.index(self.get_action())
        o = self.pomdp.observations.index(observation)
        b = self.pomdp.belief_update(a, o, self.current_belief)
        self.reset(belief=b)

    def _rec_trajectory_tree(self, obs, horizon):
        if horizon >= 0:
            try:
                b = self.current_belief
                self.step(obs)
                tree = self.trajectory_tree(horizon)
                self.reset(belief=b)  # Restore state for next obs
                return tree
            except Impossible:  # Observation is impossible here
                pass
        return None  # either horizon is reached or observation is impossible

    def trajectory_tree(self, horizon):
        obs = self.pomdp.observations
        children = [self._rec_trajectory_tree(o, horizon - 1) for o in obs]
        return {"belief": self.current_belief.tolist(),
                "action": self.get_action(),
                "node": int(self.current),
                "observations": [o for i, o in enumerate(obs)
                                 if children[i] is not None],
                "children": [c for c in children if c is not None],
                }

    def trajectory_trees_from_starts(self, horizon=5):
        start = self.pomdp.start
        trees = []
        for s in start.nonzero():
            b = np.zeros(start.shape)
            b[s] = 1.
            self.reset(belief=b)
            trees.append(self.trajectory_tree(horizon))
        return {"graphs": trees}

    def save_trajectories_from_starts(self, dest, horizon=5, indent=None):
        with open(dest, 'w') as f:
            json.dump(self.trajectory_trees_from_starts(horizon=horizon),
                      f, indent=indent)

    def visit(self, max_states=100):
        v = _Aux(self, max_nodes=max_states)
        v.visit()
        return GraphPolicy(v.actions, v.observations, np.asarray(v.trans),
                           np.vstack(v.nodes), init=0)


class _Aux:

    tol = 1.e-2

    def __init__(self, pgbr, max_nodes=100):
        self.pr = pgbr
        self.max_nodes = max_nodes
        self.nodes = []
        self.queue = Queue()  # FIFO
        self.trans = []
        self.actions = []

    @property
    def observations(self):
        return self.pr.pomdp.observations

    @property
    def beliefs(self):
        return np.vstack(self.nodes)

    def closest(self, b):
        if len(self.nodes) < 1:
            return -1, np.inf
        else:
            distances = np.sqrt(((self.beliefs - b) ** 2).sum(-1))
            i = distances.argmin()
            return i, distances[i]

    def index(self, b):
        i, d = self.closest(b)
        if d < self.tol:
            return i
        else:
            i = len(self.nodes)
            self.nodes.append(b)
            self.trans.append([None for _ in self.observations])
            self.pr.reset(np.array(b))
            self.actions.append(self.pr.get_action())
            self.queue.put(i)
            return i

    def visit(self):
        self.index(self.pr.pomdp.start)
        while not (self.queue.empty() or len(self.nodes) > self.max_nodes):
            ib = self.queue.get()
            for io, o in enumerate(self.observations):
                try:
                    self.pr.reset(belief=np.array(self.nodes[ib]))
                    self.pr.step(o)
                    ib_new = self.index(self.pr.current_belief)
                    self.trans[ib][io] = int(ib_new)
                except Impossible:
                    pass


# POMCP

class _SearchTree:

    def __init__(self, model, horizon, exploration,
                 relative_exploration=False):
        self.model = model
        self.root = self._observation_node_for_belief(ArrayBelief(model.start))
        self.horizon = horizon
        self.exploration = exploration
        self.relative_explo = relative_exploration
        # TODO: add option for particle belief (or decide depending on model)

    def get_node(self, history):
        """Raises ValueError if node does not exist or history is invalid."""
        node = self.root
        for i, h in enumerate(history):
            try:
                node = node.children[h]
            except KeyError:
                node = None
            if node is None:
                raise ValueError('{} is not a valid child at {} in {}'.format(
                    h, i, history))
        return node

    def random_action(self):
        return np.random.randint(self.model.n_actions)

    def rollout_from_node(self, node, state, horizon):
        if horizon == 0:
            return 0
        else:
            full_return = 0.
            gamma = 1.
            while horizon > 0:
                horizon -= 1
                a = self.random_action()
                state, _, r = self.model.sample_transition(a, state)
                full_return += gamma * r
                gamma *= self.model.discount
            node.update(full_return)
            return full_return

    def simulate_from_node(self, node):
        state = node.belief.sample()
        self._simulate_from_node(node, state, self.horizon)

    def _observation_node_for_belief(self, b):
        return _SearchObservationNode(b, self.model.n_actions)

    def _simulate_from_node(self, node, state, horizon):
        if horizon == 0:
            return node.value
        else:
            a = node.get_best_action(exploration=self.exploration,
                                     relative_exploration=self.relative_explo)
            child = node.safe_get_child(a)
            new_s, o, r = self.model.sample_transition(a, state)
            if o not in child.children:
                # Create node with updated belief
                child.children[o] = self._observation_node_for_belief(
                    node.belief.successor(self.model, a, o))
                # Use rollout
                partial_return = self.rollout_from_node(
                    child.children[o], new_s, horizon - 1)
            else:
                # Continue regular search
                partial_return = self._simulate_from_node(
                    child.children[o], new_s, horizon - 1)
            full_return = r + self.model.discount * partial_return
            child.update(full_return)
            node.update(full_return)
            # TODO belief update (not needed for exact belief)
            return full_return

    def to_dict(self, as_policy=False):
        return self.root.to_dict(self.model, as_policy=as_policy)


class _ObservationLookupSearchTree(_SearchTree):

    def __init__(self, model, horizon, exploration,
                 relative_exploration=False):
        self._obs_nodes = {}  # used in super for root initialization
        super(_ObservationLookupSearchTree, self).__init__(
            model, horizon, exploration,
            relative_exploration=relative_exploration)

    def _observation_node_for_belief(self, b):
        # Returns node for given belief, creating one if none exists
        if b not in self._obs_nodes:
            self._obs_nodes[b] = _SearchObservationNode(
                b, self.model.n_actions)
        return self._obs_nodes[b]

    # Here we need to keep track of visited children since the tree is no more
    # a tree...
    def to_dict(self, as_policy=False):
        return self.root.to_dict(self.model, as_policy=as_policy,
                                 exclude_visited=set())


class _ValueAverage(object):

    def __init__(self, alpha=0):
        self.n_simulations = 0
        self.total_value = 0.
        assert(0 <= alpha <= 1)
        self.alpha = alpha

    @property
    def value(self):
        return 0. if self.n_simulations == 0 \
                else self.total_value / self.n_simulations

    def update(self, value):
        self.total_value = ((self.total_value + value) * (1 - self.alpha)
                            + self.alpha * self.n_simulations * value)
        self.n_simulations += 1


class _SearchNode(object):

    def __init__(self):
        self._avg = _ValueAverage(alpha=0)
        self.children = {}

    def __str__(self):
        return "[" + ", ".join(["{}: {}".format(i, self.children[i])
                                for i in self._children_keys()]) + "]"

    def _children_keys(self):
        return sorted(self.children.keys())

    @property
    def n_simulations(self):
        return self._avg.n_simulations

    @property
    def value(self):
        return self._avg.value

    def update(self, value):
        self._avg.update(value)

    def to_dict(self, model, as_policy=False, exclude_visited=None):
        return {"value": self.value,
                "visits": self.n_simulations,
                "node": None,
                }


class _SearchObservationNode(_SearchNode):
    """
    Children indexed by action.
    """

    def __init__(self, belief, n_actions):
        super(_SearchObservationNode, self).__init__()
        self.belief = belief
        self.children = [None for _ in range(n_actions)]

    def children_dict(self, model):
        return {model.actions[a]: c
                for a, c in enumerate(self.children) if c is not None}

    def _children_keys(self):
        return [i for i, c in enumerate(self.children) if c is not None]

    def _not_init_children(self):
        return [i for i, c in enumerate(self.children)
                if c is None or c.n_simulations == 0]

    def augmented_values(self, exploration=0, relative=False):
        # Note: nans are returned for not initialized children
        if exploration > 0 and relative:
            vals = [child.value if child is not None else np.nan
                    for child in self.children]
            exploration *= np.nanmax(vals) - np.nanmin(vals)
        l_ns = np.log(self.n_simulations)
        return [child.value + exploration * np.sqrt(l_ns / child.n_simulations)
                if child is not None else np.nan
                for child in self.children]

    def get_best_action(self, exploration=0, relative_exploration=False):
        not_init = self._not_init_children()
        if len(not_init) == 0:
            assert(self.n_simulations > 0)  # explored if children explored
            # Augmented greedy (UCT)
            a = np.argmax([self.augmented_values(
                exploration=exploration, relative=relative_exploration)])
        else:
            # Chose an unexplored action
            a = np.random.choice(not_init)
        return a

    def safe_get_child(self, a):
        if self.children[a] is None:
            self.children[a] = _SearchActionNode()
        return self.children[a]

    def to_dict(self, model, as_policy=False, observed=None,
                exclude_visited=None):
        children = True
        if exclude_visited is not None:
            if self.belief in exclude_visited:
                children = False
            else:
                exclude_visited.add(self.belief)
        base = super(_SearchObservationNode, self).to_dict(
            model, as_policy=as_policy, exclude_visited=exclude_visited)
        base["belief"] = self.belief.to_list()
        if as_policy:
            a = self.get_best_action()
            grand_children = self.safe_get_child(a).children
            base.update({
                "action": model.actions[a],
                "observed": observed,
                "values": [v if not math.isnan(v) else None
                           for v in self.augmented_values()],  # For json
                "exploration_terms": [
                    np.sqrt(np.log(self.n_simulations) / child.n_simulations)
                    if ((child is not None) and child.n_simulations > 0)
                    else None
                    for child in self.children
                    ],
                "child_visits": [c.n_simulations if c is not None else 0
                                 for c in self.children],
                })
            if children:
                base.update({
                    "observations": [model.observations[o]
                                     for o in grand_children],
                    "children": [
                        grand_children[o].to_dict(
                            model, as_policy=as_policy, observed=i,
                            exclude_visited=exclude_visited)
                        for i, o in enumerate(grand_children)],
                    })
        else:
            if children:
                base.update({
                    "actions": [model.actions[i]
                                for i, c in enumerate(self.children)
                                if c is not None],
                    "children": [c.to_dict(model, as_policy=as_policy,
                                           exclude_visited=exclude_visited)
                                 for c in self.children if c is not None],
                    })
            else:
                base.update({'actions': [], 'children': []})

        return base


class _SearchActionNode(_SearchNode):
    """
    Children indexed by observation.
    """

    def to_dict(self, model, as_policy=False, exclude_visited=None):
        if as_policy:
            raise NotImplemented
        else:
            base = super(_SearchActionNode, self).to_dict(
                model, as_policy=as_policy, exclude_visited=exclude_visited)
            base.update({
                "observations": [model.observations[o]
                                 for o in self.children],
                "children": [self.children[o].to_dict(
                                model, as_policy=as_policy,
                                exclude_visited=exclude_visited)
                             for o in self.children],
                })
            return base


class BaseBelief:

    def sample(self):
        raise NotImplemented

    def successor(self, model):
        raise NotImplemented


class ArrayBelief:

    def __init__(self, probabilities):
        self.array = np.asarray(probabilities)
        _assert_normal(self.array, 'probabilities')

    def __hash__(self):
        return hash(self.array.tostring())

    def __eq__(self, other):
        return (isinstance(other, ArrayBelief)
                and (self.array == other.array).all())

    def sample(self):
        return np.random.choice(self.array.shape[0], p=self.array)

    def successor(self, model, a, o):
        return ArrayBelief(model.belief_update(a, o, self.array))

    def to_list(self):
        return self.array.tolist()


class ParticleBelief:
    pass


class POMCPPolicyRunner(object):
    """
    :param particles: number of particles for belief estimation
    :param horizon: length of simulation episodes
    :param iterations: number of simulation episodes to run
    :param exploration: UCT exploration parameter (c in [Silver2010])
    :param belief_values: group values for histories with same belief
    """

    def __init__(self, model, particles=20, iterations=100, horizon=100,
                 exploration=None, relative_exploration=False,
                 belief_values=False):
        if exploration is None:
            exploration = 1. if relative_exploration else 100
        tree_class = (_ObservationLookupSearchTree if belief_values
                      else _SearchTree)
        self.tree = tree_class(model, horizon, exploration,
                               relative_exploration=relative_exploration)
        self.iterations = iterations
        # TODO particles
        self.reset()

    @property
    def actions(self):
        return self.tree.model.actions

    @property
    def observations(self):
        return self.tree.model.observations

    def reset(self, belief=None):
        if belief is None:
            belief = self.tree.model.start
        self.history = []
        self._last_action = None

    def get_action(self):
        # Note iterations must be greater than the number of actions
        # to guarantee that any action chosen as best_action is explored first
        node = self.tree.get_node(self.history)
        for _ in range(self.iterations):
            self.tree.simulate_from_node(node)
        a = node.get_best_action()
        # No exploration during exploitation?
        self._last_action = a
        return self.actions[a]

    def step(self, observation):
        if self._last_action is None:
            raise ValueError('Unknown last action')
            # TODO rethink the design of the PolicyRunner class
        o = self.observations.index(observation)
        self.history.extend([self._last_action, o])

    def trajectory_trees_from_starts(self, qvalue=False):
        return {"graphs": [self.tree.to_dict(as_policy=not qvalue)]}
