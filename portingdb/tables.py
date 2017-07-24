from sqlalchemy import Column, ForeignKey, MetaData, extract, desc
from sqlalchemy import UniqueConstraint
from sqlalchemy.types import Boolean, Integer, Unicode, UnicodeText, Date, DateTime
from sqlalchemy.types import Enum
from sqlalchemy.orm import backref, relationship
from sqlalchemy.orm.collections import mapped_collection
from sqlalchemy.ext.declarative import declarative_base

metadata = MetaData()
TableBase = declarative_base(metadata=metadata)

def IDColumn():
    return Column(
        'id',
        Integer(), primary_key=True, nullable=False, autoincrement=True,
        doc=u"An internal numeric ID")


class NamingData(TableBase):
    u"""Static data for naming policy."""
    __tablename__ = 'naming'
    ident = Column(
        Unicode(), primary_key=True, nullable=False,
        doc=u"Machine-friendly name")
    name = Column(
        Unicode(), nullable=False,
        doc=u"Display name")
    color = Column(
        Unicode(), nullable=False,
        doc=u"Color for reports (RRGGBB)")
    term = Column(
        Unicode(), nullable=False,
        doc=u"Terminal representation")
    violation = Column(
        Unicode(), nullable=False,
        doc=u"Violation explanation")
    description = Column(
        Unicode(), nullable=False,
        doc=u"Textual description of the name status")
    short_description = Column(
        Unicode(), nullable=False,
        doc=u"Textual short description of the name status")

    def __repr__(self):
        return '<{} {}>'.format(type(self).__qualname__, self.ident)

    def __str__(self):
        return 'Naming: {}'.format(self.name)


class Status(TableBase):
    u"""State a package can be in."""
    __tablename__ = 'statuses'
    ident = Column(
        Unicode(), primary_key=True, nullable=False,
        doc=u"Machine-friendly name")
    name = Column(
        Unicode(), nullable=False,
        doc=u"Display name")
    abbrev = Column(
        Unicode(), nullable=False,
        doc=u"Abbreviation for reports")
    color = Column(
        Unicode(), nullable=False,
        doc=u"Color for reports (RRGGBB)")
    order = Column(
        Integer(), nullable=False,
        doc=u"Index for sorting (for progress bars)")
    weight = Column(
        Integer(), nullable=False,
        doc=u"Weight for sorting packages (for package lists)")
    term = Column(
        Unicode(), nullable=False,
        doc=u"Terminal representation")
    description = Column(
        Unicode(), nullable=False,
        doc=u"Textual description of the status")
    rank = Column(
        Integer(), nullable=False,
        doc=u"Rank for summarizing package state across collections. Higher rank trumps a lower one")
    instructions = Column(
        Unicode(), nullable=False,
        doc=u"Description on what to do with a package in this status")

    def __repr__(self):
        return '<{} {}>'.format(type(self).__qualname__, self.ident)

    def __str__(self):
        return 'Status: {}'.format(self.name)


class Priority(TableBase):
    u"""Priority a package can have."""
    __tablename__ = 'priorities'
    ident = Column(
        Unicode(), primary_key=True, nullable=False,
        doc=u"Machine-friendly name")
    name = Column(
        Unicode(), nullable=False,
        doc=u"Display name")
    abbrev = Column(
        Unicode(), nullable=False,
        doc=u"Abbreviation for reports")
    color = Column(
        Unicode(), nullable=False,
        doc=u"Color for reports (RRGGBB)")
    order = Column(
        Integer(), nullable=False,
        doc=u"Index for sorting")
    weight = Column(
        Integer(), nullable=False,
        doc=u"Weight for sorting packages")
    term = Column(
        Unicode(), nullable=False,
        doc=u"Terminal representation")

    def __repr__(self):
        return '<{} {}>'.format(type(self).__qualname__, self.ident)

    def __str__(self):
        return '{} priority'.format(self.name)


class Package(TableBase):
    u"""An abstract "package", for grouping similar packages in different Collections"""
    __tablename__ = 'packages'
    name = Column(
        Unicode(), primary_key=True, nullable=False,
        doc=u"The package name")
    status = Column(
        Unicode(), ForeignKey(Status.ident), nullable=False,
        doc=u"Summarized status")
    loc_python = Column(
        Integer(), nullable=True,
        doc="Approximate number of Python lines of code")
    loc_capi = Column(
        Integer(), nullable=True,
        doc="Approximate number of C lines of code that uses the CPython API")
    loc_total = Column(
        Integer(), nullable=True,
        doc="Approximate total number of lines of code (in any language)")
    loc_version = Column(
        Integer(), nullable=True,
        doc="Package version for which line-of-code stats were gathered")

    by_collection = relationship(
        'CollectionPackage',
        collection_class=mapped_collection(lambda cp: cp.collection.ident))
    status_obj = relationship(
        'Status', backref=backref('packages'))

    def __repr__(self):
        return '<{} {}>'.format(type(self).__qualname__, self.name)

    @property
    def pending_requirements(self):
        return [r for r in self.requirements
                if r.status not in ('released', 'dropped')]

    @property
    def pending_requirers(self):
        return [r for r in self.requirers
                if r.status not in ('released', 'dropped')]

    @property
    def nonblocking(self):
        return any(cp.nonblocking for cp in self.collection_packages)

    @property
    def list_tracking_bugs(self):
        return [tb.url for cp in self.collection_packages for tb in cp.tracking_bugs]

    @property
    def last_link_update(self):
        values = [link.last_update for cp in self.collection_packages
                for link in cp.links if link.last_update is not None]
        if values:
            return values[0]
        else:
            return None


class Collection(TableBase):
    u"""A distro, or non-distro repository (e.g. "fedora" or "upstream")."""
    __tablename__ = 'collections'
    ident = Column(
        Unicode(), primary_key=True, nullable=False,
        doc=u"Machine-friendly name")
    name = Column(
        Unicode(), nullable=False,
        doc=u"Display name")
    order = Column(
        Integer(), nullable=False,
        doc=u"Index for sorting")
    description = Column(
        Unicode())

    def status_description(self, status):
        if status is None:
            return 'Unknown'
        for cs in self.collection_statuses:
            if cs.status == status.ident:
                return cs.description
        return status.description

    def __repr__(self):
        return '<{} {}>'.format(type(self).__qualname__, self.ident)


class CollectionStatus(TableBase):
    """Information about what a status means in a particular collection"""
    __tablename__ = 'collection_statuses'
    id = IDColumn()
    collection_ident = Column(
        Unicode(), ForeignKey(Collection.ident), nullable=False)
    status = Column(
        Unicode(), ForeignKey(Status.ident), nullable=False)
    description = Column(
        Unicode())

    collection = relationship(
        Collection, backref=backref('collection_statuses'))
    status_obj = relationship(
        Status, backref=backref('collection_statuses'))


class CollectionPackage(TableBase):
    u"""Data for a package in a particular collection."""
    __tablename__ = 'collection_packages'
    __table_args__ = (
        UniqueConstraint('collection_ident', 'package_name'),
        UniqueConstraint('collection_ident', 'name'),
    )
    id = IDColumn()
    package_name = Column(
        Unicode(), ForeignKey(Package.name), index=True, nullable=False)
    collection_ident = Column(
        Unicode(), ForeignKey(Collection.ident), nullable=False)
    name = Column(
        Unicode(), nullable=False,
        doc=u"The package name, as it appears in this collection")
    status = Column(
        Unicode(), ForeignKey(Status.ident), nullable=False)
    priority = Column(
        Unicode(), ForeignKey(Priority.ident), nullable=False)
    deadline = Column(
        Date(), nullable=True,
        doc=u"Tentative porting deadline")
    note = Column(
        Unicode())
    nonblocking = Column(
        Boolean(), default=False,
        doc=u"If true, does not block dependent packages (even if it's marked as unported)")
    is_misnamed = Column(
        Boolean(), doc=u"True if the package does not follow the naming policy")

    package = relationship(
        'Package',
        backref=backref('collection_packages'))
    collection = relationship(
        'Collection', backref=backref('collection_packages'))
    status_obj = relationship(
        'Status', backref=backref('collection_packages'))
    priority_obj = relationship(
        'Priority', backref=backref('collection_packages'))

    def __repr__(self):
        return '<{} {} for {}>'.format(type(self).__qualname__, self.name,
                                       self.collection.ident)


class Dependency(TableBase):
    u"""Dependency link between packages."""
    __tablename__ = 'dependencies'
    __table_args__ = {'sqlite_autoincrement': True}
    id = IDColumn()
    requirer_name = Column(
        Unicode(), ForeignKey(Package.name), index=True, nullable=False,
        doc=u"The package that depends on another one")
    requirement_name = Column(
        Unicode(), ForeignKey(Package.name), index=True, nullable=False,
        doc=u"The package that is depended upon")
    unversioned = Column(
        Boolean(), default=False,
        doc=u"True if the requirement name should be changed to a versioned one")

    requirer = relationship(
        'Package', backref=backref('requirement_dependencies'),
        foreign_keys=[requirer_name])
    requirement = relationship(
        'Package', backref=backref('requirer_dependencies'),
        foreign_keys=[requirement_name])

    def __repr__(self):
        return '<{} {} on {}>'.format(type(self).__qualname__,
                                      self.requirer_name,
                                      self.requirement_name)


class RPM(TableBase):
    u"""Package RPM."""
    __tablename__ = 'rpms'
    __table_args__ = {'sqlite_autoincrement': True}
    id = IDColumn()
    collection_package_id = Column(
        ForeignKey(CollectionPackage.id), nullable=False)
    rpm_name = Column(
        Unicode(), index=True, nullable=False)
    is_misnamed = Column(
        Boolean(), doc=u"True if the package does not follow the naming policy")

    collection_package = relationship(
        'CollectionPackage', backref=backref('rpms', order_by=rpm_name))

    def __repr__(self):
        return '<{} {} for {}>'.format(type(self).__qualname__,
                                       self.rpm_name,
                                       self.collection_package.name)


class TrackingBug(TableBase):
    u"""Tracking bugs associated with a package."""
    __tablename__ = 'tracking_bugs'
    __table_args__ = (UniqueConstraint('collection_package_id', 'url'),
                      {'sqlite_autoincrement': True})

    id = IDColumn()
    collection_package_id = Column(
        ForeignKey(CollectionPackage.id), nullable=False)
    url = Column(
        Unicode(), nullable=False,
        doc='URL of the tracking bug')

    collection_package = relationship(
        'CollectionPackage', backref=backref('tracking_bugs'))

    def __repr__(self):
        return '<{} for {}: {}>'.format(type(self).__qualname__,
                                           self.collection_package.name,
                                           self.url)


class Link(TableBase):
    u"""URL associated with a package."""
    __tablename__ = 'links'
    __table_args__ = (UniqueConstraint('collection_package_id', 'url'),
                      {'sqlite_autoincrement': True})
    id = IDColumn()
    collection_package_id = Column(
        ForeignKey(CollectionPackage.id), nullable=False)
    url = Column(
        Unicode(), nullable=False)
    type = Column(
        Enum('homepage', 'bug', 'repo'), nullable=False)
    note = Column(
        Unicode(), nullable=True,
        doc='Type-specific note about the link')
    last_update = Column(
        DateTime(), nullable=True,
        doc="Datetime of the last known change of the Link's contents")


    collection_package = relationship(
        'CollectionPackage', backref=backref('links'))

    def __repr__(self):
        return '<{} {} for {}: {}>'.format(type(self).__qualname__, self.type,
                                           self.collection_package.name,
                                           self.url)


class Group(TableBase):
    __tablename__ = 'groups'
    ident = Column(
        Unicode(), primary_key=True, nullable=False,
        doc=u"Machine-friendly name")
    name = Column(
        Unicode(), nullable=False,
        doc=u"Display name")
    hidden = Column(
        Boolean(), nullable=False, default=False,
        doc=u"True if the group should not be shown on the main page")

    def __repr__(self):
        return '<{} {}>'.format(type(self).__qualname__, self.ident)


class GroupPackage(TableBase):
    __tablename__ = 'group_packages'
    id = IDColumn()
    __table_args__ = (UniqueConstraint('group_ident', 'package_name'), )
    group_ident = Column(
        ForeignKey(Group.ident), nullable=False)
    package_name = Column(
        ForeignKey(Package.name), nullable=False)
    is_seed = Column(
        Boolean(), nullable=False, default=False)

    group = relationship(
        'Group', backref=backref('group_packages'))
    package = relationship(
        'Package', backref=backref('group_packages'))


class PyDependency(TableBase):
    __tablename__ = 'py_dependencies'
    name = Column(
        Unicode(), primary_key=True, nullable=False)
    py_version = Column(
        Integer(), nullable=False)

    def __repr__(self):
        return '<{} {} ( py{})>'.format(type(self).__qualname__, self.name,
                                        self.py_version)


class RPMPyDependency(TableBase):
    __tablename__ = 'rpm_py_dependencies'
    id = IDColumn()
    __table_args__ = (UniqueConstraint('py_dependency_name', 'rpm_id'), )
    py_dependency_name = Column(
        ForeignKey(PyDependency.name), nullable=False)
    rpm_id = Column(
        ForeignKey(RPM.id), nullable=False)

    py_dependency = relationship(
        'PyDependency', backref=backref('rpm_py_dependencies'))
    rpm = relationship(
        'RPM', backref=backref('rpm_py_dependencies'))


class Config(TableBase):
    u"""Stores configuration."""
    # This is here so that the database has all the required data.
    __tablename__ = 'config'
    key = Column(
        Unicode(), primary_key=True, nullable=False)
    value = Column(
        Unicode(), nullable=False)


class HistoryEntry(TableBase):
    u"""Stores the database's history."""
    # This is here so that the database has all the required data.
    __tablename__ = 'history_entries'
    id = IDColumn()
    __table_args__ = (UniqueConstraint('commit', 'status'), )
    commit = Column(
        Unicode(), nullable=False)
    status = Column(
        ForeignKey(Status.ident), nullable=False)
    date = Column(
        Unicode(), nullable=False)
    num_packages = Column(
        Integer(), nullable=False)

    status_obj = relationship(
        'Status', backref=backref('history'))


class HistoryNamingEntry(TableBase):
    u"""Stores the database's history for naming policy page."""
    __tablename__ = 'history_naming_entries'
    id = IDColumn()
    __table_args__ = (UniqueConstraint('commit', 'status'), )
    commit = Column(
        Unicode(), nullable=False)
    status = Column(
        ForeignKey(NamingData.ident), nullable=False)
    date = Column(
        Unicode(), nullable=False)
    num_packages = Column(
        Integer(), nullable=False)

    status_obj = relationship(
        'NamingData', backref=backref('history'))


Package.requirements = relationship(
    Package,
    secondary=Dependency.__table__,
    primaryjoin=Package.name == Dependency.requirer_name,
    secondaryjoin=Package.name == Dependency.requirement_name,
    backref="requirers")

Package.groups = relationship(
    Group,
    secondary=GroupPackage.__table__,
    primaryjoin=Package.name == GroupPackage.package_name,
    secondaryjoin=Group.ident == GroupPackage.group_ident,
    backref="packages")

RPM.py_dependencies = relationship(
    PyDependency,
    secondary=RPMPyDependency.__table__,
    primaryjoin=RPM.id == RPMPyDependency.rpm_id,
    secondaryjoin=PyDependency.name == RPMPyDependency.py_dependency_name,
    backref="rpms")
