#!/bin/bash

# Update portingdb data
# Run with: $ ./scripts/update_portingdb.sh 


confirm () {
    # Alert the user what they are about to do.
    echo "About to: $@"
    # Ask if they wish to continue.
    read -r -p "Continue? [y/N/skip] " response
    case $response in
        [yY][eE][sS]|[yY])
            # If yes, then go on.
            ;;
        [nN][oO]|[nN])
            # If no, exit.
            echo "Bye!"
            exit
            ;;
        skip)
            echo "Skipping"
            return 1
            ;;
        *)
            # Or ask again.
            echo "Wat again? :/"
            confirm $@
            
    esac
}

echo -e "----------------------- Step 1 ----------------------------"
echo -e "Get the Python 3 porting status using 'py3query' dnf plugin"
dnf-3 --disablerepo='*' --enablerepo=rawhide --enablerepo=rawhide-source py3query --refresh -o data/fedora.json

echo -e "\n----------------------- Step 2 ----------------------------"
echo -e "Update historical status data (data/history.csv)"
python3 -u scripts/get-history.py --update data/history.csv | tee history.csv &&
mv history.csv data/history.csv

echo -e "Update historical status data for naming (data/history-naming.csv)"
python3 -u scripts/get-history.py -n --update data/history-naming.csv | tee history-naming.csv &&
mv history-naming.csv data/history-naming.csv

echo -e "\n---------------------- Step 3 ----------------------------"
echo -e "Load the newly generated data into the database"
python3 -m portingdb -v --datadir=data/ load

echo -e "\n---------------------- Step 4 ----------------------------"
echo -e "Compare statuses of packages across two JSON files"
python3 scripts/jsondiff.py <(git show HEAD:data/fedora.json) data/fedora.json
echo "ACTION REQUIRED: Take a closer look at the above output!"

echo -e "\n---------------------- Step 5 ----------------------------"
confirm "Commit changes" &&
git add data/history.csv data/fedora.json data/history-naming.csv && git commit -m 'Update Fedora data'

echo -e "\n---------------------- Step 6 ----------------------------"
confirm "Push changes" &&
git push origin master
