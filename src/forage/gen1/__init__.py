"""Forage Gen-1 package.

Submodules are imported explicitly. Do not import the payment app here:
watch/metrics processes must load `ch_traffic` / `ch_discovery` without
touching receiver wallet files.
"""
