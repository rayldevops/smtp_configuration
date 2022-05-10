from odoo import api, fields, models, tools, _
from odoo.tools import ustr, pycompat, formataddr
from email.utils import getaddresses
import logging
from email.message import EmailMessage
import email.policy
from email.utils import make_msgid
import html2text
import datetime


import re




address_pattern = re.compile(r'([^ ,<@]+@[^> ,]+)')
_logger = logging.getLogger(__name__)



def encapsulate_email(old_email, new_email):
    """Change the FROM of the message and use the old one as name.

    e.g.
    * Old From: "Admin" <admin@gmail.com>
    * New From: notifications@odoo.com
    * Output:   "Admin (admin@gmail.com)" <notifications@odoo.com>
    """
    old_email_split = getaddresses([old_email])
    if not old_email_split or not old_email_split[0]:
        return old_email

    new_email_split = getaddresses([new_email])
    if not new_email_split or not new_email_split[0]:
        return

    if old_email_split[0][0]:
        name_part = '%s (%s)' % old_email_split[0]
    else:
        name_part = old_email_split[0][1]

    return formataddr((
        name_part,
        new_email_split[0][1],
    ))

def email_domain_extract(email):
    """Return the domain of the given email."""
    if not email:
        return

    email_split = getaddresses([email])
    if not email_split or not email_split[0]:
        return

    _, _, domain = email_split[0][1].rpartition('@')
    return domain

def extract_rfc2822_addresses(text):
    """Returns a list of valid RFC2822 addresses
       that can be found in ``source``, ignoring
       malformed ones and non-ASCII ones.
    """
    if not text:
        return []
    candidates = address_pattern.findall(ustr(text))
    return [formataddr(('', c), charset='ascii') for c in candidates]

class InheritIrMailServer(models.Model):
    _inherit = 'ir.mail_server'

    from_filter = fields.Char(string='From Filter',required=1)

    def build_email(self, email_from, email_to, subject, body, email_cc=None, email_bcc=None, reply_to=False,
                    attachments=None, message_id=None, references=None, object_id=False, subtype='plain', headers=None,
                    body_alternative=None, subtype_alternative='plain'):
        """Constructs an RFC2822 email.message.Message object based on the keyword arguments passed, and returns it.

           :param string email_from: sender email address
           :param list email_to: list of recipient addresses (to be joined with commas)
           :param string subject: email subject (no pre-encoding/quoting necessary)
           :param string body: email body, of the type ``subtype`` (by default, plaintext).
                               If html subtype is used, the message will be automatically converted
                               to plaintext and wrapped in multipart/alternative, unless an explicit
                               ``body_alternative`` version is passed.
           :param string body_alternative: optional alternative body, of the type specified in ``subtype_alternative``
           :param string reply_to: optional value of Reply-To header
           :param string object_id: optional tracking identifier, to be included in the message-id for
                                    recognizing replies. Suggested format for object-id is "res_id-model",
                                    e.g. "12345-crm.lead".
           :param string subtype: optional mime subtype for the text body (usually 'plain' or 'html'),
                                  must match the format of the ``body`` parameter. Default is 'plain',
                                  making the content part of the mail "text/plain".
           :param string subtype_alternative: optional mime subtype of ``body_alternative`` (usually 'plain'
                                              or 'html'). Default is 'plain'.
           :param list attachments: list of (filename, filecontents) pairs, where filecontents is a string
                                    containing the bytes of the attachment
           :param list email_cc: optional list of string values for CC header (to be joined with commas)
           :param list email_bcc: optional list of string values for BCC header (to be joined with commas)
           :param dict headers: optional map of headers to set on the outgoing mail (may override the
                                other headers, including Subject, Reply-To, Message-Id, etc.)
           :rtype: email.message.EmailMessage
           :return: the new RFC2822 email message
        """
        email_from = email_from or self._get_default_from_address()
        _logger.info('email %s',email_from)
        assert email_from, "You must either provide a sender address explicitly or configure "\
                           "using the combination of `mail.catchall.domain` and `mail.default.from` "\
                           "ICPs, in the server configuration file or with the "\
                           "--email-from startup parameter."

        headers = headers or {}         # need valid dict later
        email_cc = email_cc or []
        email_bcc = email_bcc or []
        body = body or u''

        msg = EmailMessage(policy=email.policy.SMTP)
        msg.set_charset('utf-8')

        if not message_id:
            if object_id:
                message_id = tools.generate_tracking_message_id(object_id)
            else:
                message_id = make_msgid()
        msg['Message-Id'] = message_id
        if references:
            msg['references'] = references
        msg['Subject'] = subject
        email_from, return_path = self._get_email_from(email_from)
        msg['From'] = email_from
        del msg['Reply-To']
        msg['Reply-To'] = reply_to or email_from
        msg['To'] = email_to
        if email_cc:
            msg['Cc'] = email_cc
        if email_bcc:
            msg['Bcc'] = email_bcc
        msg['Date'] = datetime.datetime.utcnow()
        for key, value in headers.items():
            msg[pycompat.to_text(ustr(key))] = value

        email_body = ustr(body)
        if subtype == 'html' and not body_alternative:
            msg.add_alternative(html2text.html2text(email_body), subtype='plain', charset='utf-8')
            msg.add_alternative(email_body, subtype=subtype, charset='utf-8')
        elif body_alternative:
            msg.add_alternative(ustr(body_alternative), subtype=subtype_alternative, charset='utf-8')
            msg.add_alternative(email_body, subtype=subtype, charset='utf-8')
        else:
            msg.set_content(email_body, subtype=subtype, charset='utf-8')

        if attachments:
            for (fname, fcontent, mime) in attachments:
                maintype, subtype = mime.split('/') if mime and '/' in mime else ('application', 'octet-stream')
                msg.add_attachment(fcontent, maintype, subtype, filename=fname)
        return msg




    def _get_email_from(self, email_from):
        """Logic which determines which email to use when sending the email.

        - If the system parameter `mail.force.smtp.from` is set we encapsulate all
          outgoing email from
        - If the previous system parameter is not set and if both `mail.dynamic.smtp.from`
          and `mail.catchall.domain` are set, we encapsulate the FROM only if the domain
          of the email is not the same as the domain of the catchall parameter
        - Otherwise we do not encapsulate the email and given email_from is used as is

        :param email_from: The initial FROM headers
        :return: The FROM to used in the headers and optionally the Return-Path
        """
        # force_smtp_from = self.env['ir.config_parameter'].sudo().get_param('mail.force.smtp.from')
        force_smtp_from = self.env['ir.mail_server'].sudo().search([('active', '=', True)], limit=1).from_filter
        _logger.info("Recording Uploaded Webhook Response Received Successfully")
        dynamic_smtp_from = self.env['ir.config_parameter'].sudo().get_param('mail.dynamic.smtp.from')
        catchall_domain = self.env['ir.config_parameter'].sudo().get_param('mail.catchall.domain')

        if force_smtp_from:
            rfc2822_force_smtp_from = extract_rfc2822_addresses(force_smtp_from)
            rfc2822_force_smtp_from = rfc2822_force_smtp_from[0] if rfc2822_force_smtp_from else None
            return encapsulate_email(email_from, force_smtp_from), rfc2822_force_smtp_from

        elif dynamic_smtp_from and catchall_domain and email_domain_extract(email_from) != catchall_domain:
            rfc2822_dynamic_smtp_from = extract_rfc2822_addresses(dynamic_smtp_from)
            rfc2822_dynamic_smtp_from = rfc2822_dynamic_smtp_from[0] if rfc2822_dynamic_smtp_from else None
            return encapsulate_email(email_from, dynamic_smtp_from), rfc2822_dynamic_smtp_from

        return email_from, None
