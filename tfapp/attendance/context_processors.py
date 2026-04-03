def pending_approvals(request):
    if not request.user.is_authenticated:
        return {"pending_approval_counts": None}
    from attendance.views import get_pending_approval_counts_for_user

    return {"pending_approval_counts": get_pending_approval_counts_for_user(request.user)}
