"""Self-contained UI scenes (full-screen modes) for the pygame client.

Each scene owns its state, input handling, and rendering, and is driven by the host
GameUI (which provides the surface, fonts, and shared services). This keeps ui.py from
absorbing every screen; new scenes can be added here and delegated to from the host.
"""
