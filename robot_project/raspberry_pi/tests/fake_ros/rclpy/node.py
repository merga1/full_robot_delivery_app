from types import SimpleNamespace as NS
from ._fut import Future

class _Pub:
    def __init__(self): self.published=[]
    def publish(self, m): self.published.append(m)

class _Client:
    def __init__(self): self.ready=False; self.calls=0; self.next_state=3
    def service_is_ready(self): return self.ready
    def call_async(self, req):
        self.calls+=1; f=Future()
        f.set_result(NS(current_state=NS(id=self.next_state))); return f

class Node:
    def __init__(self, name):
        self.name=name; self.pubs={}; self.subs={}; self.timers=[]; self.clients={}
        self.node_names=["robot_api_hub"]; self.sub_infos={}
    def create_publisher(self, t, topic, qos): self.pubs[topic]=_Pub(); return self.pubs[topic]
    def create_subscription(self, t, topic, cb, qos): self.subs[topic]=cb
    def create_timer(self, period, cb): self.timers.append((period, cb))
    def create_client(self, srv, name): self.clients[name]=_Client(); return self.clients[name]
    def get_node_names(self): return list(self.node_names)
    def get_subscriptions_info_by_topic(self, topic): return [NS(node_name=n) for n in self.sub_infos.get(topic, [])]
    def get_clock(self): return NS(now=lambda: NS(to_msg=lambda: "stamp"))
    def destroy_node(self): pass
