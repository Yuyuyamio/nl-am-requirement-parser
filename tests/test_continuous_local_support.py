import unittest
from am_print_executor.gcode_printability_gate import _continuous_local_support_covers, _segment_cells
from am_print_executor.gcode_support_continuity import ExtrusionSegment


def path(x1,y1,x2,y2,z=.4):
    return ExtrusionSegment(z,x1,y1,x2,y2,'outer wall')


class ContinuousLocalSupportTests(unittest.TestCase):
    def check(self,current,prior,support=()):
        component=set().union(*(_segment_cells(s,cell_mm=.4) for s in current))
        return _continuous_local_support_covers(component,current,prior,support,
            cell_mm=.4,model_radius_mm=.273,support_radius_mm=.56)

    def test_diagonal_grid_boundary_has_real_support(self):
        self.assertTrue(self.check([path(.41,.41,.42,.5)],[path(.38,.38,.38,.5)]))

    def test_tiny_real_air_start_is_not_exempted(self):
        self.assertFalse(self.check([path(.8,.8,.81,.81)],[path(0,0,.1,.1)]))

    def test_supported_endpoints_do_not_hide_unsupported_middle(self):
        self.assertFalse(self.check([path(0,0,2,0)],[path(0,0,.1,0),path(1.9,0,2,0)]))

    def test_radius_is_not_rounded_up_to_grid_cell(self):
        self.assertFalse(self.check([path(0,.274,2,.274)],[path(0,0,2,0)]))
        self.assertTrue(self.check([path(0,.272,2,.272)],[path(0,0,2,0)]))

    def test_support_credit_requires_supplied_reachable_paths(self):
        current=[path(0,.5,2,.5)]
        self.assertFalse(self.check(current,[]))
        self.assertTrue(self.check(current,[],[path(0,0,2,0)]))
