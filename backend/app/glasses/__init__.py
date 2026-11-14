"""What the glasses show and hear: display layouts, the wearer's questions, reminders and frames.

Every client (the browser harness at /glasses, later the Android app) is a thin shell around the
same endpoints in `app/routes/glasses.py`: it sends what the wearer said, pressed or saw, and draws
the `GlassesReply` it gets back.
"""
