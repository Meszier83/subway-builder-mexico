"""Iterative scaling improves the same dual, without changing its contract."""
import unittest
import numpy as np
from sb_mexico.demand_v2.allocation import row_normalize,warm_dual,dual_scale


class WarmStartTests(unittest.TestCase):
    def fixture(self):
        rows=np.repeat(np.arange(3),5);cols=np.tile(np.arange(5),3)
        budgets=np.array([100.,80.,20.]);targets=np.array([1.,2.,7.,30.,160.])
        groups=(rows==2).astype(int)*2+(cols>=2)
        log_prior=np.log(np.array([.1,.5,2.,70.,25.]))[cols]-.12*np.arange(15)
        group_targets=np.array([45.,135.,10.,10.]);reliability=np.array([.1,4.,.5,2.])
        return log_prior,rows,cols,budgets,targets,groups,group_targets,reliability

    def objective(self,p,args):
        prior,rows,cols,budgets,targets,groups,gt,r=args;nd=len(targets)
        _,sums=row_normalize(prior-p[:nd][cols]-p[nd:][groups],rows,budgets)
        return np.dot(budgets,sums)+np.dot(targets,p[:nd])+np.dot(r,gt*np.exp(p[nd:]/r))

    def test_each_step_decreases_original_objective_and_preserves_budgets(self):
        args=self.fixture();p=np.zeros(9);initial=self.objective(p,args)
        previous=initial
        for _ in range(30):
            old=p.copy();p=warm_dual(*args,p,iterations=1)
            self.assertLessEqual(self.objective(p,args),previous+1e-9)
            previous=self.objective(p,args)
            self.assertFalse(np.shares_memory(p,old))
        self.assertLess(previous,initial-1.)
        prior,rows,cols,budgets,targets,groups,gt,r=args
        flow,_=row_normalize(prior-p[:5][cols]-p[5:][groups],rows,budgets)
        np.testing.assert_allclose(np.bincount(rows,weights=flow),budgets,atol=1e-10)
        self.assertTrue(np.all(np.abs(p[:5])<=15.))
        self.assertTrue(np.all(np.abs(p[5:])<=15.*r))

    def test_boundaries_and_unreachable_zero_target_remain_finite(self):
        args=list(self.fixture())
        args[4]=np.r_[args[4],0.]
        initial=np.r_[np.full(6,15.),15.*args[-1]]
        p=warm_dual(*args,initial,iterations=5)
        self.assertTrue(np.isfinite(p).all())
        self.assertLessEqual(self.objective(p,args),self.objective(initial,args)+1e-9)
        self.assertEqual(p[5],initial[5])
        np.testing.assert_array_equal(initial,np.r_[np.full(6,15.),15.*args[-1]])

    def test_preconditioner_matches_dual_curvature_with_shared_row_groups(self):
        args=self.fixture();prior,rows,cols,budgets,targets,groups,gt,r=args
        p=np.linspace(-.4,.4,9);nd=len(targets)
        def gradient(x):
            flow,_=row_normalize(prior-x[:nd][cols]-x[nd:][groups],rows,budgets)
            return np.r_[targets-np.bincount(cols,weights=flow,minlength=nd),
                         gt*np.exp(x[nd:]/r)-np.bincount(groups,weights=flow,minlength=len(gt))]
        measured=[]
        for i in range(len(p)):
            plus=p.copy();minus=p.copy();plus[i]+=1e-5;minus[i]-=1e-5
            measured.append((gradient(plus)[i]-gradient(minus)[i])/2e-5)
        scale=dual_scale(prior,rows,cols,budgets,nd,groups,gt,r,p)
        np.testing.assert_allclose(scale**2*budgets.sum(),measured,rtol=1e-5,atol=1e-6)


if __name__=='__main__':unittest.main()
