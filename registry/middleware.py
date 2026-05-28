class SecurityHeadersMiddleware:
    """
    Middleware that adds custom security headers to every response to mitigate OWASP Top 10 vulnerabilities.
    """
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        
        # Clickjacking Protection
        response['X-Frame-Options'] = 'DENY'
        
        # MIME Type Sniffing Prevention
        response['X-Content-Type-Options'] = 'nosniff'
        
        # Browser-side XSS Protection
        response['X-XSS-Protection'] = '1; mode=block'
        
        # Referrer Policy
        response['Referrer-Policy'] = 'strict-origin-when-cross-origin'
        
        # Strict Content Security Policy (CSP)
        # Allows self, Bootstrap, Chart.js CDNs, Google Fonts, and base64 images
        response['Content-Security-Policy'] = (
            "default-src 'self'; "
            "script-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; "
            "style-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net https://fonts.googleapis.com; "
            "font-src 'self' https://fonts.gstatic.com; "
            "img-src 'self' data:; "
            "connect-src 'self'; "
            "frame-ancestors 'none';"
        )
        
        return response
