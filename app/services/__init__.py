"""Business services: claims, volunteer tasks and donor actions.

Each public function commits on success. On any exception it rolls the session
back and re-raises, so no half-changed state is left behind.
"""
