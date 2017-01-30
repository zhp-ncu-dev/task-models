from __future__ import print_function

import os
import logging

from htm.task import SequentialCombination, LeafCombination
from htm.supportive import (SupportivePOMDP, AssembleLeg, AssembleLegToTop,
                            NHTMHorizon)
from htm.lib.pomdp import POMCPPolicyRunner, export_pomcp


logging.getLogger().setLevel(logging.INFO)

N = 50  # for warm-up
ITERATIONS = 200
EXPLORATION = 20  # 1000
N_PARTICLES = 200
RELATIVE_EXPLO = False  # In this case use smaller exploration
BELIEF_VALUES = False
EXPORT_BELIEF_QUOTIENT = True
POMCP_DESTINATION = os.path.join(os.path.dirname(__file__),
                                 '../visualization/pomcp/json/pomcp.json')
HORIZON = 2


leg_i = 'leg-{}'.format
htm = SequentialCombination([
    SequentialCombination([
        LeafCombination(AssembleLeg(leg_i(i))),
        LeafCombination(AssembleLegToTop(leg_i(i), bring_top=(i == 0)))])
    for i in range(4)])

p = SupportivePOMDP(htm)
p.r_subtask = 0.
pol = POMCPPolicyRunner(p, iterations=ITERATIONS,
                        horizon=NHTMHorizon.generator(p, n=HORIZON),
                        exploration=EXPLORATION,
                        relative_exploration=RELATIVE_EXPLO,
                        belief_values=BELIEF_VALUES,
                        belief='particle',
                        belief_params={'n_particles': N_PARTICLES})


best = None
maxl = 0
for i in range(N):
    s = 'Exploring... [{:2.0f}%] (current best: {} [{:.1f}])'.format(
        i * 100. / N, best, pol.tree.root.children[pol._last_action].value
        if pol._last_action is not None else 0.0)
    maxl = max(maxl, len(s))
    print(' ' * maxl, end='\r')
    print(s, end='\r')
    best = pol.get_action()  # Some exploration
print('Exploring... [done]')
if BELIEF_VALUES:
    print('Found {} distinct beliefs.'.format(len(pol.tree._obs_nodes)))


export_pomcp(pol, POMCP_DESTINATION, belief_as_quotient=EXPORT_BELIEF_QUOTIENT)

# Play trajectories
for _ in range(5):
    pol.run_trajectory()

export_pomcp(pol, POMCP_DESTINATION, belief_as_quotient=EXPORT_BELIEF_QUOTIENT)
