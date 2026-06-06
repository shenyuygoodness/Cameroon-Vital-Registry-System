class SecurityHeadersMiddleware:
    """
    Adds hardened security headers to every response.
    All inline scripts have been removed from templates — no unsafe-inline needed.
    """
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)

        # Clickjacking — redundant with X_FRAME_OPTIONS setting but belt-and-suspenders
        response['X-Frame-Options'] = 'DENY'

        # MIME sniffing prevention
        response['X-Content-Type-Options'] = 'nosniff'

        # Referrer policy — also set via SECURE_REFERRER_POLICY in settings
        response['Referrer-Policy'] = 'strict-origin-when-cross-origin'

        # Permissions Policy — restrict browser features not needed by this app
        response['Permissions-Policy'] = (
            'camera=(), microphone=(), geolocation=(), '
            'payment=(), usb=(), interest-cohort=()'
        )

        # Content Security Policy
        # script-src has no unsafe-inline — XSS protection is the priority.
        # style-src allows unsafe-inline because several templates have legitimate
        # inline style attributes (e.g. style="text-align:right") that cannot be
        # removed without a full template rewrite; CSS injection is low-severity.
        # font-src includes cdn.jsdelivr.net for FontAwesome webfont files.
        response['Content-Security-Policy'] = (
            "default-src 'self'; "
            "script-src 'self' https://cdn.jsdelivr.net; "
            "style-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net https://fonts.googleapis.com; "
            "font-src 'self' https://fonts.gstatic.com https://cdn.jsdelivr.net; "
            "img-src 'self' data:; "
            "connect-src 'self' https://cdn.jsdelivr.net; "
            "frame-ancestors 'none'; "
            "base-uri 'self'; "
            "form-action 'self';"
        )

        return response
