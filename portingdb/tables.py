from sqlalchemy import Column, ForeignKey, MetaData, extract, desc
from sqlalchemy import UniqueConstraint
from sqlalchemy.types import Boolean, Integer, Unicode, UnicodeText, Date, Time
from sqlalchemy.types import Enum
from sqlalchemy.orm import backref, relationship
from sqlalchemy.ext.declarative import declarative_base

metadata = MetaData()
TableBase = declarative_base(metadata=metadata)

def IDColumn():
    return Column(
        'id',
        Integer(), primary_key=True, nullable=False, autoincrement=True,
        doc=u"An internal numeric ID")


class Package(TableBase):
    u"""An abstract "package", for grouping similar packages in different Collections"""
    __tablename__ = 'packages'
    name = Column(
        Unicode(), primary_key=True, nullable=False,
        doc=u"The package name")

    def __repr__(self):
        return '<{} {}>'.format(type(self).__qualname__, self.name)


class Collection(TableBase):
    u"""A distro, or non-distro repository (e.g. "fedora" or "upstream")."""
    __tablename__ = 'collections'
    ident = Column(
        Unicode(), primary_key=True, nullable=False,
        doc=u"Machine-friendly name")
    name = Column(
        Unicode(), nullable=False,
        doc=u"Display name")

    def __repr__(self):
        return '<{} {}>'.format(type(self).__qualname__, self.ident)


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
        doc=u"Index for sorting")
    term = Column(
        Unicode(), nullable=False,
        doc=u"Terminal representation")

    def __repr__(self):
        return '<{} {}>'.format(type(self).__qualname__, self.ident)


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
    term = Column(
        Unicode(), nullable=False,
        doc=u"Terminal representation")

    def __repr__(self):
        return '<{} {}>'.format(type(self).__qualname__, self.ident)


class CollectionPackage(TableBase):
    u"""Data for a package in a particular collection."""
    __tablename__ = 'collection_packages'
    __table_args__ = (
        UniqueConstraint('collection_ident', 'package_name'),
        UniqueConstraint('collection_ident', 'name'),
    )
    id = IDColumn()
    package_name = Column(
        Unicode(), ForeignKey(Package.name), nullable=False)
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

    package = relationship(
        'Package', backref=backref('collection_packages'))
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
        'CollectionPackage', backref=backref('rpms'))

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
