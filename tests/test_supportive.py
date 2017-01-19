from unittest import TestCase

import numpy as np

from htm.task_to_pomdp import (CollaborativeAction)
from htm.task import (SequentialCombination, AlternativeCombination,
                      LeafCombination, ParallelCombination)
from htm.supportive import (_HTMToDAG, unique, SupportivePOMDP, AssembleFoot,
                            AssembleTopJoint, AssembleLegToTop, BringTop,
                            CONSUMES, USES, CONSUMES_SOME)


class TestHelpers(TestCase):

    def test_unique(self):
        l = [2, 4, 1, 2, 4, 5, 1, 0]
        self.assertEqual(set(unique(l)), set([0, 1, 2, 4, 5]))


class TestHTMToDAG(TestCase):

    def setUp(self):
        a = CollaborativeAction('Do a', (3., 2., 5.))
        b = CollaborativeAction('Do b', (2., 3., 4.))
        c = CollaborativeAction('Do c', (2., 3., 4.))
        d = CollaborativeAction('Do d', (3., 2., 5.))
        self.l1 = LeafCombination(a)
        self.l2 = LeafCombination(b)
        self.l3 = LeafCombination(c)
        self.l4 = LeafCombination(d)

    def test_on_leaf(self):
        r = _HTMToDAG(self.l1)
        self.assertEqual(r.nodes, [self.l1])
        self.assertEqual(r.succs, [[]])
        self.assertEqual(r.init, [0])

    def test_on_sequence(self):
        res = _HTMToDAG(SequentialCombination([self.l1, self.l2, self.l3], name='Do all'))
        self.assertEqual(res.nodes, [self.l1, self.l2, self.l3])
        self.assertEqual(res.succs, [[1], [2], []])
        self.assertEqual(res.init, [0])

    def test_on_aternative(self):
        res = _HTMToDAG(AlternativeCombination([self.l1, self.l2, self.l3], name='Do any'))
        self.assertEqual(res.nodes, [self.l1, self.l2, self.l3])
        self.assertEqual(res.succs, [[], [], []])
        self.assertEqual(res.init, [0, 1, 2])

    def test_mixed(self):
        res = _HTMToDAG(SequentialCombination(
            [self.l1,
             AlternativeCombination([self.l2, self.l3], name='Do b or c'),
             self.l4,
             ], name='Do a b|c d'))
        self.assertEqual(res.nodes, [self.l1, self.l2, self.l3, self.l4])
        self.assertEqual(res.succs, [[1, 2], [3], [3], []])
        self.assertEqual(res.init, [0])

    def test_on_parallel(self):
        res = _HTMToDAG(ParallelCombination([self.l1, self.l2], name='Do any order'))
        self.assertEqual([n.name for n in res.nodes],
                         ['Do a order-0', 'Do b order-0', 'Do b order-1', 'Do a order-1'])
        self.assertEqual(res.succs, [[1], [], [3], []])
        self.assertEqual(res.init, [0, 2])


class TestSupportivePOMDP(TestCase):

    def setUp(self):
        self.bt = LeafCombination(BringTop())
        self.af = LeafCombination(AssembleFoot('leg-1'))
        self.atj = LeafCombination(AssembleTopJoint('leg-1'))
        self.alt = LeafCombination(AssembleLegToTop('leg-1'))
        self.htm = SequentialCombination([self.bt, self.af])
        self.p = SupportivePOMDP(self.htm)

    def test_populate_conditions(self):
        """Note: for this test we consider a requirement that objects
        are in this order but this is not a specification of the code.
        This test should be updated to something more accurate
        if the implementation changes.
        """
        self.assertEqual(self.p.objects, ['top', 'foot', 'leg', 'screwdriver', 'screws'])
        self.assertEqual(self.p.htm_conditions, [
            [(CONSUMES, 0)],
            [(CONSUMES, 1), (CONSUMES, 2),
             (USES, 3), (CONSUMES_SOME, 4)]])

    def test_last_actions_lead_to_final_state(self):
        self.assertEqual(self.p.htm_succs, [[1], [2]])

    def test_features(self):
        self.assertEqual(len(self.p.features), self.p.n_features)
        self.assertEqual(self.p.features, [
            'HTM', 'hold-preference', 'holding', 'top', 'foot', 'leg',
            'screwdriver', 'screws'])

    def test_actions(self):
        """Same note as test_populate_conditions."""
        self.assertEqual(len(self.p.actions), self.p.n_actions)
        self.assertEqual(self.p.actions, [
            'wait', 'hold',
            'bring top', 'remove top',
            'bring foot', 'remove foot',
            'bring leg', 'remove leg',
            'bring screwdriver', 'remove screwdriver',
            'bring screws', 'remove screws'])
        self.assertEqual(self.p.actions[self.p.A_WAIT], 'wait')
        self.assertEqual(self.p.actions[self.p.A_HOLD], 'hold')

    def test_action_ids(self):
        self.assertTrue(self.p._is_bring(self.p._bring(2)))
        self.assertFalse(self.p._is_bring(self.p._remove(2)))

    def test_object_feature(self):
        self.assertIsInstance(self.p._obj_feat(3), int)
        self.assertEqual(self.p._obj_feat(3), 6)

    def test_sample_start_no_hold(self):
        self.p.p_preferences = [0]
        s = self.p.sample_start()
        self.assertEqual(s[0], 0)
        self.assertEqual(s[1], 0)
        np.testing.assert_array_equal(s[2:], 0)
        self.assertEqual(s.dtype, np.int8)

    def test_sample_start_hold(self):
        self.p.p_preferences = [1]
        s = self.p.sample_start()
        self.assertEqual(s[0], 0)
        self.assertEqual(s[1], 1)
        np.testing.assert_array_equal(s[2:], 0)
        self.assertEqual(s.dtype, np.int8)

    def test_sample_transition(self):
        s = np.zeros((self.p.n_features,), dtype=np.int8)
        # Bring object
        a = self.p._bring(1)
        s, o, r = self.p.sample_transition(a, s)
        self.assertEqual(s[4], 1)
        self.assertEqual(o, self.p.O_NONE)
        # Remove object
        a = self.p._remove(1)
        s, o, r = self.p.sample_transition(a, s)
        self.assertEqual(s[4], 0)
        self.assertEqual(o, self.p.O_NONE)
        # Transition to new task state
        a = self.p.A_WAIT
        s, o, r = self.p.sample_transition(a, s)
        self.assertEqual(s[3], 1)  # Top is there
        self.assertEqual(o, self.p.O_NONE)