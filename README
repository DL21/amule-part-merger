Fusion de fichiers .part aMule

Script Python permettant de fusionner plusieurs fichiers .part issus du même téléchargement aMule, même lorsque leurs noms diffèrent.

Les fichiers .part.met associés sont détectés automatiquement à partir du nom du .part.

Utilisation
Vérifier une fusion
./merge_amule.py 018.part 018-1.part --dry-run


Le mode --dry-run analyse les fichiers sans créer de résultat.

Effectuer la fusion
./merge_amule.py 018.part 018-1.part


Sortie par défaut :

018-merged.part
018-merged.part.met

Choisir le nom de sortie
./merge_amule.py 018.part 018-1.part -o fusion-018


Produit :

fusion-018.part
fusion-018.part.met

Remplacer une fusion existante
./merge_amule.py 018.part 018-1.part --force

Sécurité

Le script vérifie que les .part correspondent au même téléchargement :

hash ED2K identique ;

taille identique ;

mêmes hashes de chunks ;

données communes identiques.

En cas de conflit, la fusion est interrompue.

Le fichier .part.met généré conserve les métadonnées du téléchargement et met à jour les gaps ainsi que la quantité de données disponible. Il est donc destiné à être réutilisé par aMule pour poursuivre le téléchargement.

Les fichiers sources ne sont jamais modifiés.

Conseil : toujours effectuer un --dry-run avant une première fusion.
