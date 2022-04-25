from odoo import api, fields, models, tools, _
from odoo.tools import ustr, pycompat, formataddr
from email.utils import getaddresses


import re




address_pattern = re.compile(r'([^ ,<@]+@[^> ,]+)')



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
