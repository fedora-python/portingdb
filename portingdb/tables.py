from sqlalchemy import Column, ForeignKey, MetaData, extract, desc
from sqlalchemy import UniqueConstraint
from sqlalchemy.types import Boolean, Integer, Unicode, UnicodeText, Date, Time
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

    collection_package = relationship(
        'CollectionPackage', backref=backref('rpms', order_by=rpm_name))

    def __repr__(self):
        return '<{} {} for {}>'.format(type(self).__qualname__,
                                       self.rpm_name,
                                       self.collection_package.name)


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

    collection_package = relationship(
        'CollectionPackage', backref=backref('links'))

    def __repr__(self):
        return '<{} {} for {}: {}>'.format(type(self).__qualname__, self.type,
                                           self.collection_package.name,
                                           self.url)


class Contact(TableBase):
    u"""Person associated with a package."""
    __tablename__ = 'contacts'
    __table_args__ = (UniqueConstraint('collection_package_id', 'name'), )
    id = IDColumn()
    collection_package_id = Column(
        ForeignKey(CollectionPackage.id), nullable=False)
    name = Column(
        Unicode(), nullable=False)
    role = Column(
        Enum('owner', 'manager', 'porter'),
        primary_key=True, nullable=False)

    collection_package = relationship(
        'CollectionPackage', backref=backref('contacts'))

    def __repr__(self):
        return '<{} {} for {}: {}>'.format(type(self).__qualname__, self.role,
                                           self.collection_package.name,
                                           self.name)


class Group(TableBase):
    __tablename__ = 'groups'
    ident = Column(
        Unicode(), primary_key=True, nullable=False,
        doc=u"Machine-friendly name")
    name = Column(
        Unicode(), nullable=False,
        doc=u"Display name")

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

    def __repr__(self):
        return '<{} {} for {}: {}>'.format(type(self).__qualname__, self.type,
                                           self.collection_package.name,
                                           self.url)


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
