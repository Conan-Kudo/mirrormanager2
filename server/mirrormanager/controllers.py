from turbogears.identity.exceptions import IdentityFailure
import logging
import cherrypy


import turbogears
from turbogears import controllers, expose, validate, redirect, widgets, validators, error_handler, exception_handler
from turbogears import identity
from tgfastdata import DataController
import sqlobject
from sqlobject.sqlbuilder import *
from string import strip

from mirrormanager import my_validators
from mirrormanager.model import *
from mirrormanager.lib import createErrorString
import IPy
IPy.check_addr_prefixlen = 0
from mirrormanager.categorymap import categorymap


log = logging.getLogger("mirrormanager.controllers")

def siteadmin_check(site, identity):
    if not site.is_siteadmin(identity):
        turbogears.flash("Error:You are not an admin for Site %s" % site.name)
        raise redirect("/")

def downstream_siteadmin_check(site, identity):
    if not site.is_siteadmin(identity) and not site.is_downstream_siteadmin(identity):
        turbogears.flash("Error:You are not an admin for Site %s or for a Site immediately downstream from Site %s" % (site.name, site.name))
        raise redirect("/")



# From the TurboGears book
class content:
    @turbogears.expose()
    def default(self, *vpath, **params):
        if len(vpath) == 1:
            identifier = vpath[0]
            action = self.read
            verb = 'read'
        elif len(vpath) == 2:
            identifier, verb = vpath
            verb = verb.replace('.','_')
            action = getattr(self, verb, None)
            if not action:
                raise cherrypy.NotFound
            if not action.exposed:
                raise cherrypy.NotFound
        else:
            raise cherrypy.NotFound


        if verb == "new":
            return action(**params)
        elif verb == "create":
            return action(**params)
        else:
            try:
                item=self.get(identifier)
            except sqlobject.SQLObjectNotFound:
                raise cherrypy.NotFound

            return action(item['values'], **params)




class SiteFields(widgets.WidgetsList):
    name     = widgets.TextField(validator=validators.All(validators.UnicodeString,validators.NotEmpty), label="Site Name")
    password = widgets.TextField(validator=validators.All(validators.UnicodeString,validators.NotEmpty), label="Site Password", help_text="used by report_mirrors script, you make this anything you want")
    orgUrl   = widgets.TextField(label="Organization URL", validator=validators.Any(validators.All(validators.UnicodeString,validators.URL),validators.Empty), attrs=dict(size='30'), help_text="Company/School/Organization URL e.g. http://www.redhat.com") 
    private  = widgets.CheckBox(help_text="e.g. Not available to the public")
    admin_active = widgets.CheckBox("admin_active",default=True, help_text="Clear to temporarily disable this site.")
    user_active = widgets.CheckBox(default=True, help_text="Clear to temporarily disable this site.")
    allSitesCanPullFromMe = widgets.CheckBox(label="All sites can pull from me?", default=False, help_text="Enable all mirror sites to pull from me without explicitly adding them to my list.")
    downstreamComments = widgets.TextArea(validator=validators.UnicodeString, label="Comments for downstream siteadmins.", cols='60')


site_form = widgets.TableForm(fields=SiteFields(),
                              submit_text="Save Site")


class SiteController(controllers.Controller, identity.SecureResource, content):
    require = identity.not_anonymous()

    def disabled_fields(self, site=None):
        disabled_fields = []
        if not identity.in_group("sysadmin"):
            disabled_fields.append('admin_active')
        if site is not None:
            if not site.is_siteadmin(identity):
                for a in ['password', 'user_active', 'private']:
                    disabled_fields.append(a)
            
        return disabled_fields

    def get(self, id, tg_errors=None, tg_source=None, **kwargs):
        site = Site.get(id)
        site.sqlmeta.cacheValues = False
        return dict(values=site, disabled_fields=self.disabled_fields(site=site))

    @expose(template="mirrormanager.templates.site")
    def read(self, site):
        downstream_siteadmin_check(site, identity)
        submit_action = turbogears.url("/site/%s/update" % site.id)
        return dict(form=site_form, values=site, action=submit_action, disabled_fields=self.disabled_fields(site=site))

    @expose(template="mirrormanager.templates.site")
    def new(self, **kwargs):
        submit_action = turbogears.url("/site/0/create")
        return dict(form=site_form, values=None, action=submit_action, disabled_fields=self.disabled_fields())
    
    @expose(template="mirrormanager.templates.site")
    @validate(form=site_form)
    @error_handler(new)
    def create(self, **kwargs):
        if not identity.in_group("sysadmin") and kwargs.has_key('admin_active'):
            del kwargs['admin_active']
        kwargs['createdBy'] = identity.current.user_name
        try:
            site = Site(**kwargs)
            SiteAdmin(site=site, username=identity.current.user_name)
        except: # probably sqlite IntegrityError but we can't catch that for some reason... 
            turbogears.flash("Error:Site %s already exists" % kwargs['name'])
            raise turbogears.redirect("/site/0/create")
        else:
            turbogears.flash("Site created.")
            raise turbogears.redirect("/site/%s" % site.id)

    @expose(template="mirrormanager.templates.site")
    @validate(form=site_form)
    @error_handler()
    def update(self, site, tg_errors=None, **kwargs):
        siteadmin_check(site, identity)

        if tg_errors is not None:
            errstr = ""
            for k, v in tg_errors.iteritems():
                errstr += "%s: %s\n" % (k, v)
            turbogears.flash("Error: %s" % errstr)
            return dict(form=site_form, values=site, action = turbogears.url("/site/%s/update" % site.id),
                        disabled_fields=self.disabled_fields())

        if not identity.in_group("sysadmin") and kwargs.has_key('admin_active'):
            del kwargs['admin_active']
        site.set(**kwargs)
        site.sync()
        turbogears.flash("Site Updated")
        raise turbogears.redirect("/site/%s" % site.id)

    @expose(template="mirrormanager.templates.site")
    def delete(self, site, **kwargs):
        siteadmin_check(site, identity)
        site.destroySelf()
        raise turbogears.redirect("/")

    @expose(template="mirrormanager.templates.site")
    def s2s_delete(self, site, **kwargs):
        siteadmin_check(site, identity)
        dsite = Site.get(kwargs['dsite'])
        site.del_downstream_site(dsite)
        raise turbogears.redirect("/site/%s" % site.id)


##############################################
class SiteAdminFields(widgets.WidgetsList):
    username = widgets.TextField(validator=validators.All(validators.UnicodeString,validators.NotEmpty, help_text="FAS username you wish to have be an admin for this site"))


siteadmin_form = widgets.TableForm(fields=SiteAdminFields(),
                              submit_text="Create Site Admin")


class SiteAdminController(controllers.Controller, identity.SecureResource, content):
    require = identity.not_anonymous()

    def get(self, id):
        v = SiteAdmin.get(id)
        v.sqlmeta.cacheValues = False
        return dict(values=v, site=v.site, title="Site Admin")
    
    @expose(template="mirrormanager.templates.boringsiteform")
    def new(self, **kwargs):
        siteid=kwargs['siteid']
        try:
            site = Site.get(siteid)
        except sqlobject.SQLObjectNotFound:
            raise redirect("/")

        siteadmin_check(site, identity)
        submit_action = turbogears.url("/siteadmin/0/create?siteid=%s" % siteid)
        return dict(form=siteadmin_form, values=None, action=submit_action, title="New Site Admin", site=site)
    
    @expose(template="mirrormanager.templates.boringsiteform")
    @error_handler(new)
    @validate(form=siteadmin_form)
    def create(self, **kwargs):
        if not kwargs.has_key('siteid'):
            turbogears.flash("Error: form didn't provide siteid")
            raise redirect("/")
        siteid = kwargs['siteid']

        try:
            site = Site.get(siteid)
        except sqlobject.SQLObjectNotFound:
            turbogears.flash("Error: Site %s does not exist" % siteid)
            raise redirect("/")

        siteadmin_check(site, identity)
        username = kwargs['username']
        try:
            siteadmin = SiteAdmin(site=site, username=username)
        except: # probably sqlite IntegrityError but we can't catch that for some reason... 
            turbogears.flash("Error:SiteAdmin %s already exists" % kwargs['username'])
        turbogears.flash("SiteAdmin created.")
        raise turbogears.redirect("/site/%s" % siteid)

    @expose(template="mirrormanager.templates.boringsiteform")
    def delete(self, siteadmin, **kwargs):
        site = siteadmin.my_site()
        siteadmin_check(site, identity)
        siteadmin.destroySelf()
        raise turbogears.redirect("/site/%s" % site.id)


##############################################
class SiteToSiteFields(widgets.WidgetsList):
    def get_sites_options():
        return [(s.id, s.name) for s in Site.select(orderBy='name')]

    sites = widgets.MultipleSelectField(options=get_sites_options, size=15,
                                        validator=validators.NotEmpty())
                                        

site_to_site_form = widgets.TableForm(fields=SiteToSiteFields(),
                                      submit_text="Add Downstream Site")


class SiteToSiteController(controllers.Controller, identity.SecureResource, content):
    require = identity.not_anonymous()

    def get(self, id):
        v = SiteToSite.get(id)
        v.sqlmeta.cacheValues = False
        return dict(values=v, site=v.upstream_site)
    
    @expose(template="mirrormanager.templates.boringsiteform")
    def new(self, **kwargs):
        siteid=kwargs['siteid']
        try:
            site = Site.get(siteid)
        except sqlobject.SQLObjectNotFound:
            raise redirect("/")

        siteadmin_check(site, identity)
        submit_action = turbogears.url("/site2site/0/create?siteid=%s" % siteid)
        return dict(form=site_to_site_form, values=None, action=submit_action, title="Add Downstream Site", site=site)
    
    @expose()
    @validate(form=site_to_site_form)
    @error_handler(new)
    def create(self, **kwargs):
        if not kwargs.has_key('siteid'):
            turbogears.flash("Error: form didn't provide siteid")
            raise redirect("/")
        siteid = kwargs['siteid']

        try:
            site = Site.get(siteid)
        except sqlobject.SQLObjectNotFound:
            turbogears.flash("Error: Site %s does not exist" % siteid)
            raise redirect("/")

        siteadmin_check(site, identity)
        sites = kwargs['sites']
        for dssite in sites:
            if dssite == site.id:
                continue
            try:
                site2site = SiteToSite(upstream_site=site, downstream_site=dssite)
            except: 
                pass
        turbogears.flash("SiteToSite created.")
        raise turbogears.redirect("/site/%s" % siteid)

    @expose()
    def delete(self, site2site, **kwargs):
        site = site2site.my_site()
        siteadmin_check(site, identity)
        site2site.destroySelf()
        raise turbogears.redirect("/site/%s" % site.id)



##############################################
class HostFields(widgets.WidgetsList):
    name = widgets.TextField(validator=validators.All(validators.UnicodeString,validators.NotEmpty), attrs=dict(size='30'), label="Host Name",
                             help_text="* Name of server as seen by a public end user")
    admin_active = widgets.CheckBox("admin_active", default=True, help_text="Clear to temporarily disable this host")
    user_active = widgets.CheckBox(default=True, help_text="Clear to temporarily disable this host")
    country = widgets.TextField(validator=validators.All(validators.Regex(r'^[a-zA-Z][a-zA-Z]$'),validators.NotEmpty),
                                help_text="2-letter ISO country code" )
    bandwidth_int = widgets.TextField(validator=validators.All(validators.Int, validators.NotEmpty), help_text="* integer megabits/sec, how much bandwidth you offer to a public end user")
    private = widgets.CheckBox(help_text="e.g. not available to the public, an internal private mirror")
    internet2 = widgets.CheckBox(help_text="on Internet2")
    internet2_clients = widgets.CheckBox(help_text="serves Internet2 clients, even if private")
    robot_email = widgets.TextField(validator=validators.All(validators.UnicodeString,validators.Email), help_text="email address, will receive notice of upstream content updates")
    comment = widgets.TextField(validator=validators.Any(validators.UnicodeString, validators.Empty), help_text="text, anything else you'd like a public end user to know about your mirror")

host_form = widgets.TableForm(fields=HostFields(),
                              submit_text="Save Host")

class HostController(controllers.Controller, identity.SecureResource, content):
    require = identity.not_anonymous()

    def disabled_fields(self, host=None):
        disabled_fields = []
        if not identity.in_group("sysadmin"):
            disabled_fields.append('admin_active')

        if host is not None:
            site = host.my_site()
            if not site.is_siteadmin(identity):
                for a in ['user_active', 'private', 'robot_email']:
                    disabled_fields.append(a)
        return disabled_fields


    def get(self, id):
        host = Host.get(id)
        host.sqlmeta.cacheValues = False
        return dict(values=host)

    @expose(template="mirrormanager.templates.host")
    def new(self, **kwargs):
        try:
            siteid=kwargs['siteid']
            site = Site.get(siteid)
        except sqlobject.SQLObjectNotFound:
            raise redirect("/")
        submit_action = turbogears.url("/host/0/create?siteid=%s" % siteid)
        return dict(form=host_form, values=None, action=submit_action, disabled_fields=self.disabled_fields(),
                    title="Create Host", site=Site.get(siteid))

    @expose(template="mirrormanager.templates.host")
    @validate(form=host_form)
    @error_handler()
    def create(self, **kwargs):
        if not identity.in_group("sysadmin") and kwargs.has_key('admin_active'):
            del kwargs['admin_active']
        site = Site.get(kwargs['siteid'])
        del kwargs['siteid']
        try:
            host = Host(site=site, **kwargs)
        except: # probably sqlite IntegrityError but we can't catch that for some reason... 
            turbogears.flash("Error:Host %s already exists" % kwargs['name'])
            submit_action = turbogears.url("/host/0/create?siteid=%s" % site.id)
            return dict(form=host_form, values=None, action=submit_action, disabled_fields=self.disabled_fields(),
                        title="Create Host", site=site)
        
        
        turbogears.flash("Host created.")
        raise turbogears.redirect("/host/%s" % host.id)


    @expose(template="mirrormanager.templates.host")
    def read(self, host):
        downstream_siteadmin_check(host.my_site(), identity)
        submit_action = turbogears.url("/host/%s/update" % host.id)
        return dict(form=host_form, values=host, action=submit_action,
                    disabled_fields=self.disabled_fields(host=host), title="Host", site=host.site)

    @expose(template="mirrormanager.templates.host")
    @validate(form=host_form)
    @error_handler()
    def update(self, host, tg_errors=None, **kwargs):
        siteadmin_check(host.my_site(), identity)

        if tg_errors is not None:
            errstr = ""
            for k, v in tg_errors.iteritems():
                errstr += "%s: %s\n" % (k, v)
            turbogears.flash("Error: %s" % errstr)
            return dict(form=host_form, values=host, action = turbogears.url("/host/%s/update" % host.id),
                        disabled_fields=self.disabled_fields(host=host), title="Host", site=host.site)


        if not identity.in_group("sysadmin") and kwargs.has_key('admin_active'):
            del kwargs['admin_active']
        host.set(**kwargs)
        host.sync()
        turbogears.flash("Host Updated")
        raise turbogears.redirect("/host/%s" % host.id)

    @expose()
    def delete(self, host, **kwargs):
        siteadmin_check(host.my_site(), identity)
        siteid = host.site.id
        host.destroySelf()
        raise turbogears.redirect("/site/%s" % siteid)


##################################################################33
# HostCategory
##################################################################33
class HostCategoryFieldsNew(widgets.WidgetsList):
    def get_category_options():
        return [(c.id, c.name) for c in Category.select(orderBy='name')]
    category = widgets.SingleSelectField(options=get_category_options,
                                         validator=validators.NotEmpty())
    admin_active = widgets.CheckBox(default=True, help_text="unused")
    user_active = widgets.CheckBox(default=True, help_text="Clear to temporarily disable this category")
    upstream = widgets.TextField(validator=validators.Any(validators.UnicodeString,validators.Empty), attrs=dict(size='30'), help_text='e.g. rsync://download.fedora.redhat.com/fedora-linux-core')
    always_up2date = widgets.CheckBox(default=False, help_text="Set to force belief that the whole category is always in sync.")

class LabelObjName(widgets.Label):
    template = """
    <label xmlns:py="http://purl.org/kid/ns#"
    id="${field_id}"
    class="${field_class}"
    py:if="value is not None"
    py:content="value.name"
    />
    """
#leave this here so emacs highlighting works"
class InvalidData(Exception):
    pass

class HostCategoryFieldsRead(widgets.WidgetsList):
    category = LabelObjName()
    admin_active = widgets.CheckBox(default=True)
    user_active = widgets.CheckBox(default=True)
    upstream = widgets.TextField(attrs=dict(size='30'), validator=validators.Any(validators.UnicodeString,validators.Empty),
                                 help_text='e.g. rsync://download.fedora.redhat.com/fedora-linux-core')
    always_up2date = widgets.CheckBox(default=False, help_text="Set to force belief that the whole category is always in sync.  Be careful with this.")

host_category_form_new = widgets.TableForm(fields=HostCategoryFieldsNew(),
                                       submit_text="Save Host Category")

host_category_form_read = widgets.TableForm(fields=HostCategoryFieldsRead(),
                                            submit_text="Save Host Category")



class HostCategoryController(controllers.Controller, identity.SecureResource, content):
    require = identity.not_anonymous()

    def disabled_fields(self, host=None):
        disabled_fields = []
        if not identity.in_group("sysadmin"):
            disabled_fields.append('admin_active')
            disabled_fields.append('always_up2date')
        return disabled_fields

    def get(self, id):
        hc = HostCategory.get(id)
        hc.sqlmeta.cacheValues = False
        return dict(values=hc)

    @expose(template="mirrormanager.templates.hostcategory")
    def new(self, **kwargs):

        try:
            hostid=kwargs['hostid']
            host = Host.get(hostid)
        except sqlobject.SQLObjectNotFound:
            raise redirect("/")
        siteadmin_check(host.my_site(), identity)
        submit_action = turbogears.url("/host_category/0/create?hostid=%s" % hostid)
        return dict(form=host_category_form_new, values=None, action=submit_action, disabled_fields=self.disabled_fields(), host=host)
    
    
    @expose(template="mirrormanager.templates.hostcategory")
    def read(self, hostcategory):
        downstream_siteadmin_check(hostcategory.my_site(), identity)
        submit_action = turbogears.url("/host_category/%s/update" % hostcategory.id)
        disabled_fields=self.disabled_fields()
        return dict(form=host_category_form_read, values=hostcategory, action=submit_action, disabled_fields=self.disabled_fields(), host=hostcategory.host)

    @expose(template="mirrormanager.templates.hostcategory")
    @validate(form=host_category_form_new)
    @error_handler(new)
    def create(self, **kwargs):
        if not kwargs.has_key('hostid'):
            turbogears.flash("Error: form did not provide hostid")
            raise redirect("/")
        hostid = kwargs['hostid']
        del kwargs['hostid']

        try:
            host = Host.get(hostid)
        except SQLObjectNotFound:
            turbogears.flash("Error: invalid hostid - foul play?")
            raise turbogears.redirect("/")
            
        try:
            category = Category.get(kwargs['category'])
        except SQLObjectNotFound:
            turbogears.flash("Error: invalid category - foul play?")
            raise turbogears.redirect("/host_category/0/new?hostid=%s" % hostid)
            
        del kwargs['category']

        try:
            hostcategory = HostCategory(host=host, category=category, **kwargs)
        except:
            turbogears.flash("Error: Host already has category %s.  Try again." % category.name)
            raise turbogears.redirect("/host_category/0/new?hostid=%s" % hostid)
        turbogears.flash("HostCategory created.")
        raise turbogears.redirect("/host_category/%s" % hostcategory.id)


    @expose(template="mirrormanager.templates.hostcategory")
    @validate(form=host_category_form_read)
    @error_handler()
    def update(self, hostcategory, tg_errors=None, **kwargs):
        siteadmin_check(hostcategory.my_site(), identity)
        del kwargs['category']

        if tg_errors is not None:
            errstr = ""
            for k, v in tg_errors.iteritems():
                errstr += "%s: %s\n" % (k, v)
            turbogears.flash("Error: %s" % errstr)
            return dict(form=host_category_form_read, values=hostcategory, action = turbogears.url("/host_category/%s/update" % hostcategory.id),
                        disabled_fields=self.disabled_fields(), host=hostcategory.host)
        
        
        hostcategory.set(**kwargs)
        hostcategory.sync()
        turbogears.flash("HostCategory Updated")
        raise turbogears.redirect("/")

    @expose(template="mirrormanager.templates.hostcategory")
    def delete(self, hostcategory, **kwargs):
        siteadmin_check(hostcategory.my_site(), identity)
        hostid = hostcategory.host.id
        hostcategory.destroySelf()
        raise turbogears.redirect("/host/%s" % hostid)


class HostListitemController(controllers.Controller, identity.SecureResource, content):
    require = identity.not_anonymous()
    title = ""
    form = None

    def get(self, id):
        return self.do_get(id)
    
    @expose(template="mirrormanager.templates.boringhostform")
    def new(self, **kwargs):
        try:
            hostid=kwargs['hostid']
            host = Host.get(hostid)
        except sqlobject.SQLObjectNotFound:
            raise redirect("/")

        siteadmin_check(host.my_site(), identity)
        submit_action = turbogears.url("%s/0/create?hostid=%s" % (self.submit_action_prefix, hostid))
        return dict(form=self.form, values=None, action=submit_action, title=self.title, host=host)
    
    @expose(template="mirrormanager.templates.boringhostform")
    @validate(form=form)
    @error_handler(new)
    def create(self, **kwargs):
        if not kwargs.has_key('hostid'):
            turbogears.flash("Error: form did not provide siteid")
            raise redirect("/")
        hostid = kwargs['hostid']

        try:
            host = Host.get(hostid)
        except sqlobject.SQLObjectNotFound:
            turbogears.flash("Error: Host %s does not exist" % hostid)
            raise redirect("/")

        downstream_siteadmin_check(host.my_site(), identity)

        try:
            self.do_create(host, kwargs)
        except InvalidData, msg:
            turbogears.flash(msg)
            raise turbogears.redirect("/host/%s" % host.id)
        except: # probably sqlite IntegrityError but we can't catch that for some reason... 
            turbogears.flash("Error: entity already exists")
        raise turbogears.redirect("/host/%s" % host.id)

    @expose(template="mirrormanager.templates.boringhostform")
    def delete(self, thing, **kwargs):
        host = thing.host
        siteadmin_check(host.my_site(), identity)
        thing.destroySelf()
        raise turbogears.redirect("/host/%s" % host.id)



class HostAclIPFields(widgets.WidgetsList):
    ip = widgets.TextField(label="IP", validator=validators.All(validators.UnicodeString,validators.NotEmpty))

host_acl_ip_form = widgets.TableForm(fields=HostAclIPFields(),
                                     submit_text="Create Host ACL IP")

class HostAclIPController(HostListitemController):
    submit_action_prefix = "/host_acl_ip"
    title = "New Host ACL IP"
    form = host_acl_ip_form

    def do_get(self, id):
        v = HostAclIp.get(id)
        return dict(values=v, host=v.host)

    def do_create(self, host, kwargs):
        HostAclIp(host=host, ip=kwargs['ip'])



class HostNetblockFields(widgets.WidgetsList):
    netblock = widgets.TextField(validator=validators.All(validators.UnicodeString,validators.NotEmpty))

host_netblock_form = widgets.TableForm(fields=HostNetblockFields(),
                                       submit_text="Create Host Netblock")

class HostNetblockController(HostListitemController):
    submit_action_prefix="/host_netblock"
    title = "New Host Netblock"
    form = host_netblock_form

    def do_get(self, id):
        v = HostNetblock.get(id)
        return dict(values=v, host=v.host)

    def do_create(self, host, kwargs):

        emsg = "Error: IPv4 netblocks larger than /16, and IPv6 netblocks larger than /32 can only be created by mirrormanager administrators.  Please ask mirror-admin@fedoraproject.org for assistance."

        ipv4_16 = IPy.IP('10.0.0.0/16')
        ipv6_32 = IPy.IP('fec0::/32')
        ip = IPy.IP(kwargs['netblock'])
        if ((ip.version() == 4 and ip.len() > ipv4_16.len()) or \
            (ip.version() == 6 and ip.len() > ipv6_32.len())) and \
            not identity.in_group("sysadmin"):
                raise InvalidData, emsg
        
        HostNetblock(host=host, netblock=kwargs['netblock'])

class HostCountryAllowedFields(widgets.WidgetsList):
    country = widgets.TextField(validator=validators.Regex(r'^[a-zA-Z][a-zA-Z]$'),
                                help_text="2-letter ISO country code")

host_country_allowed_form = widgets.TableForm(fields=HostCountryAllowedFields(),
                                              submit_text="Create Country Allowed")

class HostCountryAllowedController(HostListitemController):
    submit_action_prefix="/host_country_allowed"
    title = "New Host Country Allowed"
    form = host_country_allowed_form

    def do_get(self, id):
        v = HostCountryAllowed.get(id)
        return dict(values=v, host=v.host)

    def do_create(self, host, kwargs):
        HostCountryAllowed(host=host, country=kwargs['country'])



#########################################################3
# HostCategoryURL
#########################################################3
class HostCategoryUrlFields(widgets.WidgetsList):
    url = widgets.TextField(validator=validators.UnicodeString, attrs=dict(size='30'))
    private  = widgets.CheckBox(default=False, label="For other mirrors only")

host_category_url_form = widgets.TableForm(fields=HostCategoryUrlFields(),
                                               submit_text="Create URL")

class HostCategoryUrlController(controllers.Controller, identity.SecureResource, content):
    require = identity.not_anonymous()
    title = "Host Category URL"
    form = host_category_url_form

    def get(self, id):
        v = HostCategoryUrl.get(id)
        v.sqlmeta.cacheValues = False
        return dict(values=v, host_category=v.host_category)
    
    @expose(template="mirrormanager.templates.hostcategoryurl")
    def new(self, **kwargs):
        try:
            hcid=kwargs['hcid']
            host_category = HostCategory.get(hcid)
        except sqlobject.SQLObjectNotFound:
            raise redirect("/")

        host = host_category.host
        siteadmin_check(host.my_site(), identity)
            
        submit_action = turbogears.url("/host_category_url/0/create?hcid=%s" % hcid)
        return dict(form=self.form, values=None, action=submit_action, title=self.title, host_category=host_category)

    @expose(template="mirrormanager.templates.hostcategoryurl")
    @validate(form=form)
    @error_handler(new)
    def create(self, **kwargs):
        if not kwargs.has_key('hcid'):
            turbogears.flash("Error: form didn't provide hcid")
            raise redirect("/")
        hcid = kwargs['hcid']

        try:
            hc = HostCategory.get(hcid)
        except sqlobject.SQLObjectNotFound:
            turbogears.flash("Error: HostCategory %s does not exist" % hcid)
            raise redirect("/")

        siteadmin_check(hc.my_site(), identity)

        kwargs['url'] = strip(kwargs['url'])
        while kwargs['url'].endswith('/'):
            kwargs['url'] = strip(kwargs['url'][:-1])


        # This is ugly.  We've got this fake site 'Fedora Mirror'
        # which has all the hosts that didn't register themselves.
        # if someone now tries to register one, they get to the point
        # of making the hcurl, and it fails.  In this case, if the duplicate
        # belongs to a 'Fedora Mirror' site, great!  Nuke the host from
        # the Fedora Mirror site, and continue on.
        try:
            existing_hcurl = HostCategoryUrl.byUrl(kwargs['url'])
        except SQLObjectNotFound:
            pass
        else:
            otherhost = existing_hcurl.host_category.host
            othersite = otherhost.site
            if othersite.name == u'Fedora Mirror':
                otherhost.destroySelf()

        try:
            del kwargs['hcid']
            HostCategoryUrl(host_category=hc, **kwargs)
        except: # probably sqlite IntegrityError but we can't catch that for some reason... 
            turbogears.flash("Error: entity already exists")
        turbogears.flash("Success: HostCategoryURL created.")
        raise turbogears.redirect("/host_category/%s" % hcid)

    @expose(template="mirrormanager.templates.hostcategoryurl")
    def read(self, hcurl):
        downstream_siteadmin_check(hcurl.my_site(), identity)
        submit_action = turbogears.url("/host_category_url/%s/update" % hcurl.id)
        return dict(form=self.form, values=hcurl, action=submit_action, title=self.title, host_category=hcurl.host_category)
        
    @expose(template="mirrormanager.templates.hostcategoryurl")
    def update(self, hcurl, **kwargs):
        siteadmin_check(hcurl.my_site(), identity)
        if kwargs['url'].endswith('/'):
            kwargs['url'] = kwargs['url'][:-1]
        hcurl.set(**kwargs)
        hcurl.sync()
        submit_action = turbogears.url("/host_category_url/%s/update" % hcurl.id)
        return dict(form=self.form, values=hcurl, action=submit_action, title=self.title, host_category=hcurl.host_category)
        
            
    

    @expose(template="mirrormanager.templates.hostcategoryurl")
    def delete(self, hcurl, **kwargs):
        hc = hcurl.host_category
        siteadmin_check(hcurl.my_site(), identity)
        hcurl.destroySelf()
        raise turbogears.redirect("/host_category/%s" % hc.id)


#########################################################3
# SimpleDbObject
#########################################################3
class SimpleDbObjectController(controllers.Controller, identity.SecureResource, content):
    require = identity.in_group("sysadmin")
    title = "My Title"
    form = None
    myClass = None
    url_prefix=None

    def get(self, id):
        v = self.myClass.get(id)
        v.sqlmeta.cacheValues = False
        return dict(values=v)
    
    @expose(template="mirrormanager.templates.boringform")
    def new(self, **kwargs):
            
        submit_action = turbogears.url("/%s/0/create" % self.url_prefix)
        return dict(form=self.form, values=None, action=submit_action, title=self.title)

    def create(self, **kwargs):
        try:
            obj = self.myClass(**kwargs)
        except: # probably sqlite IntegrityError but we can't catch that for some reason... 
            turbogears.flash("Error: Object already exists")
            raise redirect("/%s/0/new" % self.url_prefix)
        turbogears.flash("Success: Object created.")
        raise turbogears.redirect("/")

    @expose(template="mirrormanager.templates.boringform")
    def read(self, obj):
        submit_action = turbogears.url("/%s/%s/update" % (self.url_prefix, obj.id))
        return dict(form=self.form, values=obj, action=submit_action, title=self.title)
        
    def update(self, obj, **kwargs):
        obj.set(**kwargs)
        obj.sync()
        submit_action = turbogears.url("/%s/%s/update" % (self.url_prefix, obj.id))
        return dict(form=self.form, values=obj, action=submit_action, title=self.title)

    @expose(template="mirrormanager.templates.boringform")
    def delete(self, obj, **kwargs):
        obj.destroySelf()
        raise turbogears.redirect("/")

#########################################################3
# Arch
#########################################################3

class ArchFields(widgets.WidgetsList):
    name = widgets.TextField(validator=validators.UnicodeString, attrs=dict(size='30'))

arch_form = widgets.TableForm(fields=ArchFields(), submit_text="Create Arch")

class ArchController(SimpleDbObjectController):
    title="Arch"
    myClass = Arch
    url_prefix="arch"
    form = arch_form
    
    @expose(template="mirrormanager.templates.boringform")
    @validate(form=arch_form)
    @error_handler(SimpleDbObjectController.new)
    def create(self, **kwargs):
        SimpleDbObjectController.create(self, **kwargs)

#########################################################3
# EmbargoedCountry
#########################################################3

class EmbargoedCountryFields(widgets.WidgetsList):
    country_code = widgets.TextField(validator=validators.Regex(r'^[a-zA-Z][a-zA-Z]$'),
                                     help_text="2-letter ISO country code" )

embargoed_country_form = widgets.TableForm(fields=EmbargoedCountryFields(), submit_text="Create Embargoed Country")

class EmbargoedCountryController(SimpleDbObjectController):
    title="Embargoed Country"
    myClass = EmbargoedCountry
    url_prefix="embargoed_country"
    form = embargoed_country_form
    
    @expose(template="mirrormanager.templates.boringform")
    @validate(form=embargoed_country_form)
    @error_handler(SimpleDbObjectController.new)
    def create(self, **kwargs):
        SimpleDbObjectController.create(self, **kwargs)


#########################################################3
# Product
#########################################################3
class ProductFields(widgets.WidgetsList):
    name = widgets.TextField(validator=validators.All(validators.UnicodeString, validators.NotEmpty),
                             attrs=dict(size='30'))

product_form = widgets.TableForm(fields=ProductFields(), submit_text="Create Product")

class ProductController(SimpleDbObjectController):
    title = "Product"
    form = product_form
    myClass = Product
    url_prefix="product"

    @expose(template="mirrormanager.templates.boringform")
    @validate(form=product_form)
    @error_handler(SimpleDbObjectController.new)
    def create(self, **kwargs):
        SimpleDbObjectController.create(self, **kwargs)


#########################################################3
# Repository
#########################################################3
class RepositoryFields(widgets.WidgetsList):
    name = widgets.TextField(validator=validators.All(validators.UnicodeString, validators.NotEmpty), attrs=dict(size='30'))
    prefix = widgets.TextField(validator=validators.All(validators.UnicodeString, validators.NotEmpty), attrs=dict(size='30'))
    category = LabelObjName()
    version = LabelObjName()
    arch = LabelObjName()
    directory = LabelObjName()

repository_form = widgets.TableForm(fields=RepositoryFields(), submit_text="Edit Repository")

class RepositoryController(SimpleDbObjectController):
    title = "Repository"
    form = repository_form
    myClass = Repository
    url_prefix="repository"

    @expose(template="mirrormanager.templates.boringform")
    @validate(form=repository_form)
    @error_handler(SimpleDbObjectController.new)
    def create(self, **kwargs):
        return SimpleDbObjectController.create(self, **kwargs)
    

    @expose(template="mirrormanager.templates.boringform")
    @validate(form=repository_form)
    @error_handler(SimpleDbObjectController.new)
    def update(self, obj, **kwargs):
        return SimpleDbObjectController.update(self, obj, **kwargs)


#########################################################3
# Version
#########################################################3
class VersionFields(widgets.WidgetsList):
    def get_products_options():
        return [(p.id, p.name) for p in Product.select(orderBy='name')]

    product = widgets.SingleSelectField(options=get_products_options,
                                        validator=validators.NotEmpty)
    name = widgets.TextField(validator=validators.UnicodeString, attrs=dict(size='30'))
    isTest = widgets.CheckBox(label="is a Test release")
    display = widgets.CheckBox(label="display in the publiclist chooser", default=True)
    display_name = widgets.TextField(validator=validators.Any(validators.UnicodeString, validators.Empty),
                                     attrs=dict(size='30'), help_text="Name displayed to users (optional)" )

version_form = widgets.TableForm(fields=VersionFields(), submit_text="Create Version")


class VersionController(SimpleDbObjectController):
    title = "Version"
    myClass = Version
    url_prefix="version"
    form = version_form

    @expose(template="mirrormanager.templates.boringform")
    @validate(form=form)
    @error_handler(SimpleDbObjectController.new)
    def create(self, **kwargs):
        try:
            product=Product.get(kwargs['product'])
        except SQLObjectNotFound:
            turbogears.flash("Error: invalid product - foul play?")
            raise redirect("/")

        del kwargs['product']

        SimpleDbObjectController.create(self, product=product, **kwargs)

    @expose(template="mirrormanager.templates.boringform")
    @validate(form=form)
    @error_handler()
    def update(self, obj, **kwargs):
        return SimpleDbObjectController.update(self, obj, **kwargs)

#########################################################3
# RepositoryRedirect
#########################################################3
class RepositoryRedirectFields(widgets.WidgetsList):
    fromRepo = widgets.TextField(validator=validators.All(validators.NotEmpty, validators.UnicodeString)) 
    toRepo = widgets.TextField(validator=validators.All(validators.NotEmpty, validators.UnicodeString)) 

repository_redirect_form = widgets.TableForm(fields=RepositoryRedirectFields(), submit_text="Create Repository Redirect")

class RepositoryRedirectController(SimpleDbObjectController):
    title = "RepositoryRedirect"
    myClass = RepositoryRedirect
    url_prefix="repository_redirect"
    form = repository_redirect_form

    @expose(template="mirrormanager.templates.boringform")
    @validate(form=form)
    @error_handler(SimpleDbObjectController.new)
    def create(self, **kwargs):
        SimpleDbObjectController.create(self, **kwargs)

    @expose(template="mirrormanager.templates.boringform")
    @validate(form=form)
    @error_handler()
    def update(self, obj, **kwargs):
        return SimpleDbObjectController.update(self, obj, **kwargs)

#########################################################3
# CountryContinentRedirect
#########################################################3
class CountryContinentRedirectFields(widgets.WidgetsList):
    country = widgets.TextField(validator=validators.All(validators.NotEmpty, validators.UnicodeString)) 
    continent = widgets.TextField(validator=validators.All(validators.NotEmpty, validators.UnicodeString)) 

country_continent_redirect_form = widgets.TableForm(fields=CountryContinentRedirectFields(), submit_text="Create Country->Continent Redirect")

class CountryContinentRedirectController(SimpleDbObjectController):
    title = "CountryContinentRedirect"
    myClass = CountryContinentRedirect
    url_prefix="country_continent_redirect"
    form = country_continent_redirect_form

    @expose(template="mirrormanager.templates.boringform")
    @validate(form=form)
    @error_handler(SimpleDbObjectController.new)
    def create(self, **kwargs):
        SimpleDbObjectController.create(self, **kwargs)

    @expose(template="mirrormanager.templates.boringform")
    @validate(form=form)
    @error_handler()
    def update(self, obj, **kwargs):
        return SimpleDbObjectController.update(self, obj, **kwargs)


class PublicListController(controllers.Controller):
    @expose(template="mirrormanager.templates.publiclist")
    def index(self, *vpath, **params):
        hosts = hosts=[h for h in Host.select(orderBy='country') if not h.is_private() and h.is_active()]
        
        return dict(hosts=hosts, numhosts=len(hosts),
                    products=list(Product.select(orderBy='name')), title='', arches=display_publiclist_arches, product=None, ver=None, arch=None, valid_categories=None)

    @expose(template="mirrormanager.templates.publiclist")
    def default(self, *vpath, **params):
        product = ver = arch = None
        if len(vpath) == 1:
            product = vpath[0]
            title=product
        elif len(vpath) == 2:
            product = vpath[0]
            ver = vpath[1]
            title = '%s/%s' % (product, ver)
        elif len(vpath) == 3:
            product = vpath[0]
            ver = vpath[1]
            arch = vpath[2]
            title = '%s/%s/%s' % (product, ver, arch)
        else:
            raise redirect('/publiclist')

        hosts = []
        try:
            hosts = [Host.get(hostId[0]) for hostId in publiclist_hosts(product, ver, arch)]
        except:
            pass

        valid_categories = categorymap(product, ver)

        return dict(hosts=hosts, numhosts=len(hosts),
                    products=list(Product.select(orderBy='name')),
                    arches=display_publiclist_arches, title=title, product=product, ver=ver, arch=arch, valid_categories=valid_categories)



class Root(controllers.RootController):
    site = SiteController()
    siteadmin = SiteAdminController()
    host = HostController()
    host_country_allowed = HostCountryAllowedController()
    host_acl_ip = HostAclIPController()
    host_netblock = HostNetblockController()
    host_category = HostCategoryController()
    host_category_url = HostCategoryUrlController()
    site2site = SiteToSiteController()
    product = ProductController()
    version = VersionController()
    arch = ArchController()
    embargoed_country = EmbargoedCountryController()
    repository = RepositoryController()
    from mirrormanager.xmlrpc import XmlrpcController
    xmlrpc = XmlrpcController()
    publiclist = PublicListController()
    
    @expose(template="mirrormanager.templates.welcome")
    @identity.require(identity.not_anonymous())
    def index(self):
        if "sysadmin" in identity.current.groups:
            sites = Site.select(orderBy='name')
        else:
            sites = user_sites(identity)
        return {"sites":sites}

    @expose(template="mirrormanager.templates.adminview")
    @identity.require(identity.in_group('sysadmin'))
    def adminview(self):
        return {"sites":Site.select(orderBy='name'),
                "arches":Arch.select(),
                "products":Product.select(),
                "versions":Version.select(),
                "directories":Directory.select(orderBy='name'),
                "categories":Category.select(),
                "repositories":Repository.select(orderBy='name'),
                "embargoed_countries":EmbargoedCountry.select(),
                "netblocks":HostNetblock.select(orderBy='host_id'),
                "repository_redirects":RepositoryRedirect.select(orderBy='fromRepo'),
                "country_continent_redirects":CountryContinentRedirect.select(orderBy='country'),
                }

    @expose(template="mirrormanager.templates.rsync_acl", format="plain", content_type="text/plain")
    def rsync_acl(self, **kwargs):
        internet2_only=False
        public_only=False
        if 'internet2_only' in kwargs:
            internet2_only = True
        if 'public_only' in kwargs:
            public_only = True

        result = rsync_acl_list(internet2_only=internet2_only, public_only=public_only)
        return dict(values=result)

    @expose(template="mirrormanager.templates.login")
    def login(self, forward_url=None, previous_url=None, *args, **kw):

        if not identity.current.anonymous \
            and identity.was_login_attempted() \
            and not identity.get_identity_errors():
            raise redirect(forward_url)

        forward_url=None
        previous_url= cherrypy.request.path

        if identity.was_login_attempted():
            msg=_("The credentials you supplied were not correct or "
                   "did not grant access to this resource.")
        elif identity.get_identity_errors():
            msg=_("You must provide your Fedora Account System credentials before accessing "
                   "this resource.")
        else:
            msg=_("Please log in.")
            forward_url= cherrypy.request.headers.get("Referer", "/")
        cherrypy.response.status=403
        return dict(message=msg, previous_url=previous_url, logging_in=True,
                    original_parameters=cherrypy.request.params,
                    forward_url=forward_url)

    @expose()
    def logout(self):
        identity.current.logout()
        raise redirect("/")
    
    @expose(template="mirrormanager.templates.register")
    def register(self, username="", display_name="", email_address="", tg_errors=None):
        if tg_errors:
            turbogears.flash(createErrorString(tg_errors))
            username, display_name, email_address = map(cherrypy.request.input_values.get, ["username", "display_name", "email_address"])
                    
        return dict(username=username, display_name=display_name, email_address=email_address)

    @expose()
    @error_handler(register)
    @validate(validators=my_validators.RegisterSchema)
    def doRegister(self, username, display_name, password1, password2, email_address):
        username = str(username)
        email_address = str(email_address)
        redirect_to_register = lambda:redirect("/register", {"username":username, "display_name":display_name, "email_address":email_address})
        
        try:
            User.by_user_name(username)
        except sqlobject.SQLObjectNotFound:
            pass
        else:
            turbogears.flash("Error:User %s Already Exists"%username)
            raise redirect_to_register()
        
        
        try:
            User.by_email_address(email_address)
        except sqlobject.SQLObjectNotFound:
            pass
        else:
            turbogears.flash("Error:Email-Address %s Already Exists"%username)
            raise redirect_to_register()
        
        #create user
        user = User(user_name=username, email_address=email_address, display_name=str(display_name), password=str(password1))
        
        #add user to user group
        user.addGroup(Group.by_group_name("user"))
        
        raise redirect("/")
        
