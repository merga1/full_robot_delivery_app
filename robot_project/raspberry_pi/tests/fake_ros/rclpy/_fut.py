class Future:
    def __init__(self): self._r=None; self._done=False; self._cbs=[]
    def set_result(self, r):
        self._r=r; self._done=True
        for cb in list(self._cbs): cb(self)
    def done(self): return self._done
    def result(self):
        if not self._done: raise RuntimeError("not done")
        return self._r
    def add_done_callback(self, cb):
        if self._done: cb(self)
        else: self._cbs.append(cb)
