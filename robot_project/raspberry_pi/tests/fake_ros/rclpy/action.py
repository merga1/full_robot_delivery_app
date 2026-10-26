from types import SimpleNamespace as NS
from ._fut import Future

class _Handle:
    def __init__(self, accepted):
        self.accepted=accepted; self.cancelled=False; self.result_future=Future()
    def get_result_async(self): return self.result_future
    def cancel_goal_async(self): self.cancelled=True; f=Future(); f.set_result(NS()); return f

class ActionClient:
    def __init__(self, node, t, name):
        self.ready=False; self.accept=True; self.goals=[]; self.handles=[]; self.fb_cbs=[]
    def server_is_ready(self): return self.ready
    def send_goal_async(self, goal, feedback_callback=None):
        self.goals.append(goal); self.fb_cbs.append(feedback_callback)
        h=_Handle(self.accept); self.handles.append(h)
        f=Future(); f.set_result(h); return f
