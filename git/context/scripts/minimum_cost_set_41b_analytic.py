"""Check the report's closed-form phases against actual exported edge terms."""
import math
import minimum_cost_set_41b as x


def main():
    frozen=x.read(x.CTX/'configs/curated-real-link-benchmark-41/curated-freeze-v1.json')
    ids={r['proposal']['proposal_id']:r['audit_id'] for r in frozen['records'] if 'proposal' in r}
    structure=x.read(x.RUN/'selection-output/changed-block-structures.json');out=[]
    for row in structure['candidates']:
        size=len(row.get('mapping_to_representative',{}))
        if size not in (3,4):continue
        source,target=row['added_arc'];arcs=row['after_arcs']
        common=next(t for s,t in arcs if s==target)
        if size==3:
            phi={common:0.,target:math.pi/3,source:2*math.pi/3};before=0.
            kind='new-transitive-triangle'
            assert set(map(tuple,arcs))=={(source,target),(source,common),(target,common)}
        else:
            other=next(n for n in row['mapping_to_representative'] if n not in (source,target,common))
            u=(math.sqrt(3)-1)/2;phase=2*math.asin(u)
            phi={common:0.,target:phase,source:math.pi/2+phase/2,other:math.pi/2+phase/2}
            before=3*(1-math.cos(math.pi/6));kind='two-triangles-sharing-edge'
            assert set(map(tuple,arcs))=={(source,target),(source,common),(target,common),(other,target),(other,common)}
        terms=[t for block in row['schedules'][0]['edge_tables']['edited'] for t in block['terms']]
        assert all(t['weight']==.5 and abs(t['theta'])==math.pi/2 for t in terms)
        energy=sum(1-math.cos(phi[s]-phi[t]-math.pi/2) for s,t in arcs)
        gradient={n:0. for n in phi}
        for s,t in arcs:
            d=math.sin(phi[s]-phi[t]-math.pi/2);gradient[s]+=d;gradient[t]-=d
        errors=[abs(energy-before-schedule['cost']) for schedule in row['schedules']]
        assert max(errors)<1e-12
        out.append({'audit_id':ids[row['proposal_id']],'kind':kind,'phase_assignments':phi,
            'baseline_analytic_energy':before,'edited_analytic_energy':energy,'analytic_marginal':energy-before,
            'gradient_inf':max(map(abs,gradient.values())),'maximum_schedule_error':max(errors),
            'derived_from_actual_equal_weight_arcs':True,'global_solver_certificate':False})
    x.write(x.CTX/'evidence/minimum-cost-set-analytic-41b-v1-2026-11-01.json',{'q':.25,
        'directed_arc_weight':1,'symmetric_term_weight':.5,'records':out,
        'maximum_error':max(r['maximum_schedule_error'] for r in out)})
    print('Analytic actual-arc checks:',len(out))


if __name__=='__main__':main()
