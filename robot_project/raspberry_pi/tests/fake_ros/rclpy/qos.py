class QoSProfile:
    def __init__(self, **kw): self.kw=kw
class ReliabilityPolicy: RELIABLE=1; BEST_EFFORT=2
class DurabilityPolicy: TRANSIENT_LOCAL=1; VOLATILE=2
