import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from _msg import M
class Twist(M): pass
class PoseStamped(M): pass
class PoseWithCovarianceStamped(M):
    def __init__(self):
        self.pose=M(); self.pose.covariance=[0.0]*36
