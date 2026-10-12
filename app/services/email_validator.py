import re
import socket
from typing import Tuple, Optional

# Obvious dummy or test email usernames
BLOCKED_EMAIL_PREFIXES = {
    "test",
    "dummy",
    "fake",
    "sample",
    "demo",
    "admin",
    "administrator",
    "user",
    "testing",
    "example",
    "temp",
    "asdf",
    "qwerty",
    "12345",
    "123456",
    "null",
    "undefined",
    "nobody",
    "no-reply",
    "noreply",
}

# Known disposable and throwaway temporary email domains
BLOCKED_DISPOSABLE_DOMAINS = {
    "10minutemail.com",
    "10minutemail.net",
    "tempmail.com",
    "tempmail.net",
    "temp-mail.org",
    "mailinator.com",
    "guerrillamail.com",
    "guerrillamail.net",
    "guerrillamail.biz",
    "sharklasers.com",
    "getairmail.com",
    "throwawaymail.com",
    "yopmail.com",
    "yopmail.fr",
    "yopmail.net",
    "dispostable.com",
    "trashmail.com",
    "trashmail.net",
    "mohmal.com",
    "fakemailgenerator.com",
    "nada.ltd",
    "getnada.com",
    "crazymailing.com",
    "maildrop.cc",
    "inboxkitten.com",
    "mytemp.email",
    "dropmail.me",
    "fakemail.net",
    "emailondeck.com",
    "burnermail.io",
    "minuteinbox.com",
    "generator.email",
    "trashmail.de",
    "example.com",
    "example.net",
    "example.org",
    "test.com",
    "sample.com",
    "domain.com",
    "foo.com",
    "bar.com"
}

# RFC 5322 compatible email pattern
EMAIL_REGEX = re.compile(
    r"^[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+$"
)


def validate_email_authenticity(email: str) -> Tuple[bool, Optional[str]]:
    """
    Validates whether an email address is syntactically valid, not a dummy or
    disposable address, and has real mail servers (MX records) configured in DNS.

    Returns:
        (is_valid, error_message)
    """
    if not email or not isinstance(email, str):
        return False, "Email address is required."

    clean = email.strip().lower()

    # 1. Basic format & length check
    if len(clean) > 254:
        return False, "Email address is too long."

    if not EMAIL_REGEX.match(clean):
        return False, "Please enter a valid email address format (e.g. name@domain.com)."

    parts = clean.split("@")
    if len(parts) != 2:
        return False, "Invalid email format."

    local_part, domain = parts[0], parts[1]

    # 2. Dummy username pattern check (e.g. test@gmail.com, dummy@yahoo.com)
    # Check exact match or prefixes like test1234, dummy_user
    if local_part in BLOCKED_EMAIL_PREFIXES:
        return False, f"Dummy email address '{clean}' is not permitted. Please use your real personal or work email."

    for prefix in ("test.", "test_", "dummy.", "dummy_", "fake.", "fake_"):
        if local_part.startswith(prefix):
            return False, f"Dummy email address '{clean}' is not permitted. Please provide an active email account."

    # 3. Check for disposable / temporary email domains
    if domain in BLOCKED_DISPOSABLE_DOMAINS:
        return False, f"Temporary / disposable email domains (@{domain}) are not allowed. Please use a permanent email account."

    # 4. Check domain syntax
    if "." not in domain or domain.endswith("."):
        return False, f"The email domain '{domain}' is invalid."

    tld = domain.split(".")[-1]
    if len(tld) < 2:
        return False, f"The domain extension '.{tld}' is invalid."

    # 5. DNS MX (Mail Exchange) Record Verification
    # Confirms that the domain actually exists and has mail servers configured to receive mail.
    try:
        import dns.resolver

        resolver = dns.resolver.Resolver()
        resolver.timeout = 3.0
        resolver.lifetime = 3.0

        try:
            mx_records = resolver.resolve(domain, "MX")
            if not mx_records or len(mx_records) == 0:
                return False, f"The domain '@{domain}' has no active mail exchange (MX) servers and cannot receive email."
        except dns.resolver.NoAnswer:
            # Domain exists but no MX; check if A record exists (fallback per RFC 5321)
            try:
                a_records = resolver.resolve(domain, "A")
                if not a_records or len(a_records) == 0:
                    return False, f"The domain '@{domain}' does not accept incoming emails."
            except Exception:
                return False, f"The domain '@{domain}' does not have active mail servers."
        except dns.resolver.NXDOMAIN:
            return False, f"The domain '@{domain}' does not exist on the internet. Please check for typos."
        except (dns.resolver.NoNameservers, dns.resolver.Timeout):
            # If DNS resolution times out, fallback to socket host check
            try:
                socket.gethostbyname(domain)
            except socket.gaierror:
                return False, f"Unable to resolve domain '@{domain}'. Please verify your email domain."

    except ImportError:
        # Fallback if dnspython is unavailable
        try:
            socket.gethostbyname(domain)
        except socket.gaierror:
            return False, f"The email domain '@{domain}' cannot be found."

    return True, None
