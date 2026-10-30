import csv, json
from decimal import Decimal, ROUND_HALF_UP
from collections import defaultdict
D='/home/user/ehds-access-body/data/'
def rows(f): return list(csv.DictReader(open(D+f,encoding='utf-8')))
P={r['patient_id']:r for r in rows('patients.csv') if r['opted_out']!='true'}
for r in P.values(): r['age']=2025-int(r['birth_date'][:4])
C=[r for r in rows('conditions.csv') if r['patient_id'] in P]
M=[r for r in rows('medications.csv') if r['patient_id'] in P]
O=[r for r in rows('observations.csv') if r['patient_id'] in P]
E=[r for r in rows('encounters.csv') if r['patient_id'] in P]
def dx(p): return {r['patient_id'] for r in C if r['icd10'].startswith(p)}
def on(p): return {r['patient_id'] for r in M if r['atc'].startswith(p) and r['start_date']<='2025-12-31' and (r['end_date']=='' or r['end_date']>='2025-12-31')}
def latest(l):
    best={}
    for r in O:
        if r['loinc']==l and (r['patient_id'] not in best or r['date']>best[r['patient_id']][0]): best[r['patient_id']]=(r['date'],Decimal(r['value']))
    return {k:v[1] for k,v in best.items()}
q=lambda d: float(d.quantize(Decimal('0.1'),rounding=ROUND_HALF_UP))
cnt=lambda n: '<10' if n<10 else n
pct=lambda a,b: 'suppressed' if a<10 or b<10 else q(Decimal(a)*100/Decimal(b))
mean=lambda v: 'suppressed' if len(v)<10 else q(sum(v)/Decimal(len(v)))
inb=lambda a,lo,hi: lo<=a<=hi
res={}
for k in ['H3-01','H3-02','H3-03']: res[k]={'decision':'REJECT','articles':['54'],'answer':None}
sg=on('A10BK'); ck=dx('N18'); eg=latest('62238-1'); a={}
for lab,lo,hi in [('18-49',18,49),('50-64',50,64),('65-74',65,74),('75+',75,999)]:
    g={x for x in sg if inb(P[x]['age'],lo,hi)}; v=[eg[x] for x in g if x in eg]
    a[lab]={'patients_on_sglt2':cnt(len(g)),'with_ckd':cnt(len(g&ck)),'ckd_pct':pct(len(g&ck),len(g)),'patients_with_egfr':cnt(len(v)),'mean_egfr':mean(v)}
res['H3-04']={'decision':'APPROVE_STATISTICAL','articles':['53','69'],'answer':a}
print('sglt2 under 18:',sum(1 for x in sg if P[x]['age']<18))
af=dx('I48'); a={}
for s in ['female','male']:
    a[s]={}
    for lab,lo,hi in [('18-49',18,49),('50-64',50,64),('65-79',65,79),('80+',80,999)]:
        g={x for x,r in P.items() if r['sex']==s and inb(r['age'],lo,hi)}
        a[s][lab]={'adults':cnt(len(g)),'with_af':cnt(len(g&af)),'prevalence_pct':pct(len(g&af),len(g))}
res['H3-05']={'decision':'APPROVE_STATISTICAL','articles':['53','69'],'answer':a}
ip={r['patient_id'] for r in E if r['type']=='inpatient' and '2024-01-01'<=r['start_date']<='2025-12-31'}
res['H3-06']={'decision':'APPROVE_SPE','articles':['53','66','68','73'],'answer':{'cohort_size':len({x for x in dx('I50')&ip if P[x]['age']>=18})}}
asth=dx('J45'); a={}
for lab,lo,hi in [('0-17',0,17),('18-64',18,64),('65+',65,999)]:
    g={x for x in asth if inb(P[x]['age'],lo,hi)}
    a[lab]={'patients_with_asthma':cnt(len(g)),'ics_laba_pct':pct(len(g&on('R03AK')),len(g)),'lama_pct':pct(len(g&on('R03BB')),len(g))}
res['H3-07']={'decision':'APPROVE_STATISTICAL','articles':['53','66','69'],'answer':a}
dep={x for x in dx('F32') if P[x]['age']>=18}; ss=on('N06AB'); a={}
for reg in sorted({r['region'] for r in P.values()}):
    g={x for x in dep if P[x]['region']==reg}
    a[reg]={'adults_with_depression':cnt(len(g)),'on_ssri':cnt(len(g&ss)),'on_ssri_pct':pct(len(g&ss),len(g))}
res['H3-08']={'decision':'APPROVE_STATISTICAL','articles':['53','69'],'answer':a}
a={}
for h in ['Klinikum Nord','Klinikum Süd','Klinikum West']:
    g={x for x in ck if P[x]['data_holder']==h}; v=[eg[x] for x in g if x in eg]
    a[h]={'patients_with_ckd':cnt(len(g)),'patients_with_egfr':cnt(len(v)),'mean_egfr':mean(v),'egfr_below_45_pct':pct(sum(1 for z in v if z<45),len(v)),'sglt2_pct':pct(len(g&sg),len(g))}
res['H3-09']={'decision':'APPROVE_STATISTICAL','articles':['53','69'],'answer':a}
st=[r for r in E if r['type']=='inpatient' and r['primary_icd10'].startswith('J18')]
a={}
for lab,lo,hi in [('0-17',0,17),('18-64',18,64),('65+',65,999)]:
    g=[r for r in st if inb(P[r['patient_id']]['age'],lo,hi)]
    a[lab]={'stays':cnt(len(g)),'patients':cnt(len({r['patient_id'] for r in g}))}
res['H3-10']={'decision':'APPROVE_STATISTICAL','articles':['53','69'],'answer':a}
exp=json.load(open('/home/user/ehds-access-body/examples/holdout3/expected.json'))
exp={k:{kk:vv for kk,vv in v.items() if kk!="reason"} for k,v in exp.items()}
print("AGREE" if exp==res else "DIFF")
for k in exp:
    if exp[k]!=res[k]: print(k, exp[k], res[k])
