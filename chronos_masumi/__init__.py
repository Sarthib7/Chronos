"""MIP-003 agentic service and Masumi payment integration, without the SDK.

The masumi SDK could not create payments for agents registered as
Web3CardanoV2 (it never sends supportedPaymentSourceIndex and offers no hook to
add it). This package implements the protocol directly against the payment
service's documented HTTP API.
"""
