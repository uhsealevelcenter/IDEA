station_list_appendix = """
## STATION LOOKUP APPENDIX 
Return `uhslc_id` and `name` information using the lookup table between BEGIN_STATION_JSONL and END_STATION_JSONL. Do not invent stations not in the table.

Rules:
- Normalize input by lowercasing, trimming spaces, collapsing multiple spaces, and removing punctuation (.,;:!?"'`-). Keep diacritics.  
- Allow for partial names, missing suffixes (e.g., ", HI"), and common spelling mistakes. If “Ala Wai” is given, infer “Ala Wai, HI” if that is the best match.  
- If multiple candidates are equally likely (e.g., “Pearl”), ask the user to clarify, offering up to 3 options.  
- For name lookups: return the closest matching canonical name from the table.  
- For ID lookup by name: return only the 3-digit `uhslc_id` unless more details are requested.  
- For name lookup by ID: return only the canonical `name`. 
- When necessary, return a note (e.g., "The correct spelling is...").
- If no reasonable match can be found, return “Not in FD station list.”  

### BEGIN_STATION_JSONL
{"id":"001","name":"Pohnpei"}
{"id":"002","name":"Tarawa, Bairiki"}
{"id":"003","name":"Baltra"}
{"id":"004","name":"Nauru"}
{"id":"005","name":"Majuro"}
{"id":"007","name":"Malakal, Palau"}
{"id":"008","name":"Yap"}
{"id":"009","name":"Honiara"}
{"id":"011","name":"Christmas"}
{"id":"013","name":"Kanton"}
{"id":"014","name":"French Frigate"}
{"id":"015","name":"Papeete"}
{"id":"016","name":"Rikitea"}
{"id":"017","name":"Hiva Oa"}
{"id":"018","name":"Suva"}
{"id":"019","name":"Noumea"}
{"id":"021","name":"Juan Fernandez"}
{"id":"022","name":"Easter"}
{"id":"023","name":"Rarotonga"}
{"id":"024","name":"Penrhyn"}
{"id":"025","name":"Funafuti"}
{"id":"028","name":"Saipan"}
{"id":"029","name":"Kapingamarangi"}
{"id":"030","name":"Santa Cruz"}
{"id":"031","name":"Nuku Hiva"}
{"id":"033","name":"Bitung"}
{"id":"034","name":"Cabo San Lucas"}
{"id":"035","name":"San Felix"}
{"id":"038","name":"Nuku'alofa"}
{"id":"039","name":"Kodiak Isl., AK"}
{"id":"040","name":"Adak, AK"}
{"id":"041","name":"Dutch Harbor, AK"}
{"id":"043","name":"Palmyra Island"}
{"id":"046","name":"Port Vila"}
{"id":"047","name":"Chichijima"}
{"id":"049","name":"Minamitorishima"}
{"id":"050","name":"Midway"}
{"id":"051","name":"Wake"}
{"id":"052","name":"Johnston"}
{"id":"053","name":"Guam, GU"}
{"id":"054","name":"Chuuk"}
{"id":"055","name":"Kwajalein"}
{"id":"056","name":"Pago Pago, AS"}
{"id":"057","name":"Honolulu, HI"}
{"id":"058","name":"Nawiliwili, HI"}
{"id":"059","name":"Kahului, HI"}
{"id":"060","name":"Hilo, HI"}
{"id":"061","name":"Mokuoloe, HI"}
{"id":"071","name":"Wellington"}
{"id":"072","name":"Bluff"}
{"id":"079","name":"Chatham"}
{"id":"080","name":"Antofagasta"}
{"id":"081","name":"Valparaiso"}
{"id":"082","name":"Acajutla"}
{"id":"083","name":"Arica"}
{"id":"084","name":"Lobos de Afuera"}
{"id":"087","name":"Quepos"}
{"id":"088","name":"Caldera"}
{"id":"090","name":"Socorro"}
{"id":"091","name":"La Libertad"}
{"id":"092","name":"Talara"}
{"id":"093","name":"Callao"}
{"id":"094","name":"Matarani"}
{"id":"101","name":"Mombasa"}
{"id":"103","name":"Port Louis"}
{"id":"104","name":"Diego Garcia"}
{"id":"105","name":"Rodrigues"}
{"id":"107","name":"Padang"}
{"id":"108","name":"Male"}
{"id":"109","name":"Gan"}
{"id":"110","name":"Muscat"}
{"id":"113","name":"Masirah"}
{"id":"114","name":"Salalah"}
{"id":"115","name":"Colombo"}
{"id":"117","name":"Hanimaadhoo"}
{"id":"118","name":"Ko Miang"}
{"id":"119","name":"Djibouti"}
{"id":"121","name":"Pt. La Rue"}
{"id":"122","name":"Sibolga"}
{"id":"123","name":"Sabang"}
{"id":"124","name":"Chittagong"}
{"id":"125","name":"Prigi"}
{"id":"126","name":"Jask"}
{"id":"127","name":"Syowa, Antarctica"}
{"id":"128","name":"Thevenard"}
{"id":"129","name":"Portland, S.Aus."}
{"id":"130","name":"Casey"}
{"id":"133","name":"Ambon"}
{"id":"142","name":"Langkawi"}
{"id":"147","name":"Karachi"}
{"id":"148","name":"Ko Taphao Noi"}
{"id":"149","name":"Lamu"}
{"id":"151","name":"Zanzibar"}
{"id":"153","name":"Minicoy"}
{"id":"155","name":"Dzaoudzi"}
{"id":"157","name":"Vishakhapatnam"}
{"id":"162","name":"Cilicap"}
{"id":"163","name":"Benoa"}
{"id":"164","name":"Reunion"}
{"id":"166","name":"Broome"}
{"id":"167","name":"Carnarvon"}
{"id":"168","name":"Darwin"}
{"id":"169","name":"Port Hedland"}
{"id":"170","name":"Christmas"}
{"id":"171","name":"Cocos"}
{"id":"172","name":"Aden"}
{"id":"173","name":"Davis"}
{"id":"174","name":"Cochin"}
{"id":"175","name":"Fremantle"}
{"id":"176","name":"Esperance"}
{"id":"177","name":"Mawson"}
{"id":"178","name":"Crozet"}
{"id":"179","name":"Saint Paul"}
{"id":"180","name":"Kerguelen"}
{"id":"181","name":"Durban"}
{"id":"184","name":"Port Elizabeth"}
{"id":"185","name":"Mossel Bay"}
{"id":"186","name":"Knysna"}
{"id":"187","name":"East London"}
{"id":"188","name":"Richard's Bay"}
{"id":"189","name":"Dumont d'Urville"}
{"id":"192","name":"Pemba"}
{"id":"207","name":"Ceuta"}
{"id":"209","name":"Cascais"}
{"id":"210","name":"Flores, Santa Cruz"}
{"id":"211","name":"Ponta Delgada"}
{"id":"217","name":"Las Palmas"}
{"id":"218","name":"Funchal"}
{"id":"220","name":"Walvis Bay"}
{"id":"221","name":"Simon's Town"}
{"id":"223","name":"Dakar"}
{"id":"225","name":"Sao Tome"}
{"id":"227","name":"Cayenne"}
{"id":"231","name":"Takoradi"}
{"id":"233","name":"Lagos"}
{"id":"234","name":"Pointe Noire"}
{"id":"235","name":"Palmeira"}
{"id":"242","name":"Key West, FL"}
{"id":"245","name":"San Juan, PR"}
{"id":"253","name":"Newport, RI"}
{"id":"257","name":"Settlement Point"}
{"id":"259","name":"Bermuda"}
{"id":"260","name":"Duck Pier, NC"}
{"id":"261","name":"Charleston, SC"}
{"id":"264","name":"Atlantic City, NJ"}
{"id":"266","name":"Cristobal"}
{"id":"268","name":"Limon"}
{"id":"271","name":"Fort de France"}
{"id":"273","name":"Port-aux-basques"}
{"id":"274","name":"Churchill"}
{"id":"275","name":"Halifax"}
{"id":"276","name":"St. John's"}
{"id":"280","name":"Ilha Fiscal, RJ"}
{"id":"281","name":"Cananeia"}
{"id":"283","name":"Fortaleza"}
{"id":"286","name":"Puerto Deseado"}
{"id":"288","name":"Reykjavik"}
{"id":"289","name":"Gibraltar"}
{"id":"290","name":"Port Stanley"}
{"id":"291","name":"Ascension"}
{"id":"292","name":"St. Helena"}
{"id":"293","name":"Lerwick"}
{"id":"294","name":"Newlyn, Cornwall"}
{"id":"295","name":"Stornoway"}
{"id":"299","name":"Qaqortoq"}
{"id":"302","name":"Balboa"}
{"id":"316","name":"Acapulco, Gro."}
{"id":"317","name":"Ensenada"}
{"id":"328","name":"Ko Lak"}
{"id":"329","name":"Hong Kong"}
{"id":"331","name":"Brisbane"}
{"id":"332","name":"Bundaberg"}
{"id":"333","name":"Fort Denison"}
{"id":"334","name":"Townsville"}
{"id":"335","name":"Spring Bay"}
{"id":"336","name":"Booby Island"}
{"id":"340","name":"Kaohsiung"}
{"id":"341","name":"Keelung"}
{"id":"345","name":"Nakano Shima"}
{"id":"347","name":"Abashiri"}
{"id":"348","name":"Hamada"}
{"id":"349","name":"Toyama"}
{"id":"350","name":"Kushiro"}
{"id":"351","name":"Ofunato"}
{"id":"352","name":"Mera"}
{"id":"353","name":"Kushimoto"}
{"id":"354","name":"Aburatsu"}
{"id":"355","name":"Naha"}
{"id":"356","name":"Maisaka"}
{"id":"359","name":"Naze"}
{"id":"360","name":"Wakkanai"}
{"id":"362","name":"Nagasaki"}
{"id":"363","name":"Nishinoomote"}
{"id":"364","name":"Hakodate"}
{"id":"365","name":"Ishigaki"}
{"id":"370","name":"Manila"}
{"id":"371","name":"Legaspi"}
{"id":"372","name":"Davao"}
{"id":"381","name":"Qui Nhon"}
{"id":"382","name":"Subic Bay"}
{"id":"383","name":"Vung Tau"}
{"id":"392","name":"Cocos Island"}
{"id":"395","name":"Manzanillo"}
{"id":"399","name":"Lord Howe"}
{"id":"400","name":"Lombrum"}
{"id":"401","name":"Apia"}
{"id":"402","name":"Lautoka"}
{"id":"403","name":"Jackson"}
{"id":"404","name":"Auasi, AS"}
{"id":"405","name":"Jeremie"}
{"id":"406","name":"St. Louis du Sud"}
{"id":"407","name":"Aunuu, AS"}
{"id":"408","name":"Tau, AS"}
{"id":"409","name":"Ofu, AS"}
{"id":"416","name":"Tanjung Lesung"}
{"id":"417","name":"Sadeng"}
{"id":"418","name":"Waikelo"}
{"id":"419","name":"Lembar"}
{"id":"420","name":"Saumlaki"}
{"id":"421","name":"Ala Wai, HI"}
{"id":"422","name":"Haleiwa, HI"}
{"id":"423","name":"Heeia, HI"}
{"id":"424","name":"Keehi, HI"}
{"id":"425","name":"Waianae, HI"}
{"id":"426","name":"Lahaina, HI"}
{"id":"428","name":"Kaunakakai, HI"}
{"id":"429","name":"Manele Bay, HI"}
{"id":"430","name":"Honokohau, HI"}
{"id":"431","name":"Milolii, HI"}
{"id":"432","name":"Hanalei, HI"}
{"id":"434","name":"Pearl Harbor, HI"}
{"id":"436","name":"Kamesang, Palau"}
{"id":"437","name":"Peleliu, Palau"}
{"id":"438","name":"Imekang, Palau"}
{"id":"453","name":"Leava, Futuna Island"}
{"id":"454","name":"Makemo"}
{"id":"455","name":"Rangiroa Atoll"}
{"id":"456","name":"Tubuaï"}
{"id":"540","name":"Prince Rupert"}
{"id":"541","name":"Bamfield"}
{"id":"542","name":"Tofino"}
{"id":"545","name":"Vandenberg, CA"}
{"id":"547","name":"Barbers Point, HI"}
{"id":"548","name":"Kaumalapau, HI"}
{"id":"551","name":"San Francisco, CA"}
{"id":"552","name":"Kawaihae, HI"}
{"id":"553","name":"Port Allen, HI"}
{"id":"554","name":"La Jolla, CA"}
{"id":"556","name":"Crescent City, CA"}
{"id":"558","name":"Neah Bay, WA"}
{"id":"559","name":"Sitka, AK"}
{"id":"560","name":"Seward, AK"}
{"id":"569","name":"San Diego, CA"}
{"id":"570","name":"Yakutat, AK"}
{"id":"571","name":"Ketchikan, AK"}
{"id":"574","name":"Sand Point, AK"}
{"id":"579","name":"Prudhoe Bay, AK"}
{"id":"592","name":"South Beach, OR"}
{"id":"595","name":"Nome, AK"}
{"id":"598","name":"Alofi"}
{"id":"600","name":"Ushuaia"}
{"id":"601","name":"Esperanza"}
{"id":"654","name":"Currimao"}
{"id":"655","name":"Lubang"}
{"id":"680","name":"Macquarie Is."}
{"id":"684","name":"Puerto Montt"}
{"id":"699","name":"Tanjong Pagar"}
{"id":"700","name":"Faraday"}
{"id":"701","name":"Port Nolloth"}
{"id":"702","name":"Luderitz"}
{"id":"703","name":"Saldahna Bay"}
{"id":"704","name":"Cape Town"}
{"id":"708","name":"Salvador, USCGS"}
{"id":"729","name":"Mar del Plata"}
{"id":"730","name":"Base Prat"}
{"id":"731","name":"Puerto Madryn"}
{"id":"737","name":"San Andres"}
{"id":"738","name":"Santa Marta"}
{"id":"739","name":"El Porvenir"}
{"id":"752","name":"Fort Pulaski, GA"}
{"id":"755","name":"Virginia Key, FL"}
{"id":"762","name":"Pensacola, FL"}
{"id":"767","name":"Galveston, P.Pier, TX"}
{"id":"775","name":"Galveston, Pier 21, TX"}
{"id":"776","name":"Punta Cana"}
{"id":"777","name":"Puerto Plata"}
{"id":"786","name":"Roseau"}
{"id":"789","name":"Prickley Bay"}
{"id":"795","name":"Kingstow"}
{"id":"799","name":"Portu-Prince"}
{"id":"800","name":"Andenes"}
{"id":"801","name":"Honningsvag"}
{"id":"802","name":"Maloy"}
{"id":"803","name":"Rorvik"}
{"id":"804","name":"Tregde"}
{"id":"805","name":"Vardo"}
{"id":"806","name":"Nouakchott"}
{"id":"807","name":"Alexandria"}
{"id":"808","name":"Thule (Pituffik)"}
{"id":"809","name":"Ittoqqortoormiit"}
{"id":"816","name":"Port Sonara"}
{"id":"817","name":"Upernavik harbour"}
{"id":"818","name":"Smogen"}
{"id":"819","name":"Goteborgorsh."}
{"id":"820","name":"Nuuk, Godthaab"}
{"id":"822","name":"Brest"}
{"id":"823","name":"Nylesund"}
{"id":"824","name":"Marseille"}
{"id":"825","name":"Cuxhaven"}
{"id":"826","name":"Stockholm"}
{"id":"829","name":"Trieste"}
{"id":"830","name":"La Coruna"}
{"id":"833","name":"Nain"}
{"id":"834","name":"Malin Head"}
{"id":"835","name":"Castletownbere"}
{"id":"836","name":"Alert"}
{"id":"878","name":"Bullen Bay"}
{"id":"900","name":"Inhambane"}
{"id":"906","name":"Moulmein"}
{"id":"907","name":"Akyab (Sittwe)"}
{"id":"908","name":"Port Blair"}
{"id":"913","name":"Telukdalam"}
{"id":"914","name":"Meulaboh"}
{"id":"915","name":"Chabahar"}
{"id":"920","name":"Marion Island"}
{"id":"922","name":"Mtwara"}
### END_STATION_JSONL
"""