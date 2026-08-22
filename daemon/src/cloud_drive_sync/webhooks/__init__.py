"""Outbound event webhooks.

Configuration is merged across levels by :mod:`.resolver`; the shapes live in
:mod:`.models`. Delivery is deliberately separate from resolution so the merge rules
can be tested without a network.
"""
