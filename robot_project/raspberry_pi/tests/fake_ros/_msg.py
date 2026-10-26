class M:
    """Auto-vivifying message stand-in: m.a.b = 1 just works."""
    def __getattr__(self, n):
        if n.startswith("__"): raise AttributeError(n)
        v=M(); object.__setattr__(self, n, v); return v
