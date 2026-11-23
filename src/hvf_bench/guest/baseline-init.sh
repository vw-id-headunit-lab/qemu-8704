#!/bin/sh
BB=/bin/busybox
echo 'ARM64_BASELINE_BEGIN'
$BB uname -m
$BB uname -r
echo 'ARM64_BASELINE_END'
$BB poweroff -f
while :; do $BB sleep 1; done
