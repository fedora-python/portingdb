Update portingdb data
---------------------

Setup (to be run just once and if needed)
*****************************************

To use the portingdb update scripts, you will need to install and configure the following:

#. Ensure that you run Fedora 28 or higher.

#. Install system-wide dependencies::

    $ sudo dnf install python3-bugzilla python3-libarchive-c

   Note that you cannot use Python virtual environments for this.

#. Put ``py3query.py`` in DNF plugin directory::
    
    $ sudo cp dnf-plugins/py3query.py /usr/lib/pythonX.Y/site-packages/dnf-plugins/

   Note: replace ``pythonX.Y`` with the Python version of your system.

#. Install the rawhide repo definitions::
    
    $ sudo dnf install fedora-repos-rawhide

#. Install portingdb into your virtualenv::

    (venv) $ pip install --editable .  # Mind the dot at the end!

Update the data
***************

The following steps are needed to update pordingdb data:

#. Checkout a feature branch if that's your way of doing git changes::

    $ git checkout -b update-fedora-data-...

#. Get the Python 3 porting status using ``py3query`` dnf plugin. Use ``-o`` option to write the output directly to ``fedora.json``::

    $ dnf-3 --repo=rawhide --repo=rawhide-source py3query --refresh --installroot=/tmp/empty-install-root -o data/fedora.json

#. Compare statuses of packages in the new JSON file::

    (venv) $ python scripts/jsondiff.py <(git show HEAD:data/fedora.json) data/fedora.json

#. If you're satisfied, commit the above. You can use the commit message from jsondiff:

    (venv) $ git commit -a -m"$(python scripts/jsondiff.py <(git show HEAD:data/fedora.json) data/fedora.json)"

#. Get historical status and naming policy data and update ``history.csv`` and ``history-naming.csv``::

    (venv) $ python -u scripts/get-history.py --update data/history.csv | tee history.csv && mv history.csv data/history.csv

#. Update the maintainer and orphans lists::

    $ wget https://src.fedoraproject.org/extras/pagure_owner_alias.json -O data/pagure_owner_alias.json
    $ wget https://churchyard.fedorapeople.org/orphans.json -O data/orphans.json

#. You can check how portingdb looks with the new data:

    (venv) $ python -m portingdb -v --datadir=data/ serve --debug

#. At this point, take a closer look at the jsondiff (the last commit message):

    (venv) $ git show

   For changes marked ♥, award badges: add new entries to `data/badges.txt`.

   Find responsible people at https://src.fedoraproject.org/rpms/python-FOO/commits/master

#. Commit changes::

    (venv) $ git commit -am 'Update history, badges, maintainers, orphans'

#. Push to a fork and create a PR. Put the jsondiff in the PR message; this command will put it in your clipboard (at least on X11)::

    (venv) $ git log --format=%B -n 1 HEAD~ | xsel -b

   Add (username X→Y) to the commit message for changed people in badges.txt
   Award badges to people who reached 1, 5 or 10 greened packages.

   * https://badges.fedoraproject.org/badge/parselmouth
   * https://badges.fedoraproject.org/badge/parselmouth-ii
   * https://badges.fedoraproject.org/badge/parselmouth-iii

    If you lack the permissions to award badges, note it in the PR and somebody will do it for you.


#. You made it!

