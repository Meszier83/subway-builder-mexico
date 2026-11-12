"""Exact bipartite transport through a shared tree of destination intervals."""
from array import array
from collections import Counter

import numpy as np
from scipy.sparse import coo_matrix
from scipy.sparse.csgraph import connected_components, maximum_flow


class RangeGraph:
    """Represent consecutive permitted columns without one arc per OD pair.

    Every arc from an origin enters a subtree containing only permitted jobs.
    Only subtrees actually used by an origin are connected. A feasible flow can
    therefore be decomposed back into permitted OD pairs without changing any
    row or column total.
    """

    MAX_ARCS = 5_000_000

    def __init__(self, origin_count, destination_count):
        self.origin_count = origin_count
        self.width = 1 << (max(1, destination_count)-1).bit_length()
        self.first_job = origin_count + self.width
        self.size = self.first_job + destination_count
        self.rows = array('i')
        self.cols = array('i')
        self.active = set()

    def _arc(self, origin, destination):
        if len(self.rows) >= self.MAX_ARCS:
            raise ValueError('Permitted demand support is too fragmented for the bounded range graph')
        self.rows.append(origin)
        self.cols.append(destination)

    def _activate(self, node):
        if node >= self.width or node in self.active:
            return
        self.active.add(node)
        for child in (node*2, node*2+1):
            self._arc(self.origin_count+node, self.origin_count+child)
            self._activate(child)

    def add_row(self, origin, columns):
        """Columns must be sorted and unique."""
        if not len(columns):
            return
        breaks = np.flatnonzero(np.diff(columns) != 1)
        starts = np.r_[columns[0], columns[breaks+1]]
        ends = np.r_[columns[breaks], columns[-1]] + 1
        for start, end in zip(starts, ends):
            left, right = int(start)+self.width, int(end)+self.width
            while left < right:
                if left & 1:
                    self._arc(origin, self.origin_count+left)
                    self._activate(left)
                    left += 1
                if right & 1:
                    right -= 1
                    self._arc(origin, self.origin_count+right)
                    self._activate(right)
                left //= 2
                right //= 2

    def components(self):
        graph = coo_matrix((np.ones(len(self.rows), dtype=np.int8),
                            (np.asarray(self.rows), np.asarray(self.cols))),
                           shape=(self.size, self.size)).tocsr()
        labels = connected_components(graph, directed=False, return_labels=True)[1]
        return labels[:self.origin_count], labels[self.first_job:]

    def solve(self, balances, extra=()):
        """Positive balances are supplies. Extra arcs carry explicit capacities."""
        balances = np.asarray(balances, dtype=np.int64)
        source, sink = len(balances), len(balances)+1
        origins = np.flatnonzero(balances > 0)
        destinations = np.flatnonzero(balances < 0)
        required = int(balances[origins].sum())
        if balances.sum() or required >= np.iinfo(np.int32).max:
            raise ValueError('Unsupported capacity range in bounded demand transport')
        extra_rows, extra_cols, capacities = zip(*extra) if extra else ((), (), ())
        rows = np.r_[np.asarray(self.rows), np.asarray(extra_rows, dtype=np.int32),
                     np.full(len(origins), source), destinations]
        cols = np.r_[np.asarray(self.cols), np.asarray(extra_cols, dtype=np.int32),
                     origins, np.full(len(destinations), sink)]
        values = np.r_[np.full(len(self.rows), required, dtype=np.int64),
                       np.asarray(capacities, dtype=np.int64), balances[origins], -balances[destinations]]
        matrix = coo_matrix((values, (rows, cols)), shape=(sink+1, sink+1)).tocsr()
        result = maximum_flow(matrix, source, sink, method='dinic')
        return required-int(result.flow_value), result.flow.tocsr()

    def realize(self, flow, proposal):
        """Decompose the positive tree flow and apply canceled proposal arcs."""
        result = Counter(proposal)
        for (origin, job), count in proposal.items():
            result[origin, job] -= max(0, int(flow[self.first_job+job, origin]))
        internal = {}
        for node in self.active:
            parent = self.origin_count+node
            internal[node] = [max(0, int(flow[parent, self.origin_count+child]))
                              for child in (node*2, node*2+1)]

        def add(origin, node, count):
            if node >= self.width:
                result[origin, node-self.width] += count
                return
            available = internal[node]
            for side in (0, 1):
                taken = min(available[side], count)
                if taken:
                    available[side] -= taken
                    add(origin, node*2+side, taken)
                    count -= taken
            if count:
                raise ValueError('Range transport could not decompose its integer flow')

        for origin in range(self.origin_count):
            start, stop = flow.indptr[origin:origin+2]
            for column, count in zip(flow.indices[start:stop], flow.data[start:stop]):
                if self.origin_count <= column < self.size and count > 0:
                    add(origin, int(column)-self.origin_count, int(count))
        if any(value < 0 for value in result.values()):
            raise ValueError('Range transport reversed more than its proposal')
        return +result
