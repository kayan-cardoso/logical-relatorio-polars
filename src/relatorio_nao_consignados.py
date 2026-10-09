import calendar
from datetime import date
from pathlib import Path

import polars as pl
from unidecode import unidecode

BASE_DIR = Path.cwd() / 'data_input' / 'nao_consignados' / '08'
OUT_DIR = Path.cwd() / 'data_output'

retorno = pl.read_excel(f'{BASE_DIR}/RETORNO_TODOS_09.xlsx')
base_ativos = pl.read_excel(f'{BASE_DIR}/BASE_ATIVOS_E_CANCELADOS.xlsx')
remuneracao = pl.read_excel(f'{BASE_DIR}/REMUNERACAO.xlsx')
obitos = pl.read_excel(f'{BASE_DIR}/arquivo_pdv.xlsx', sheet_name='OBITOS')
coopart = pl.read_excel(f'{BASE_DIR}/PLANILHA_COBRANÇA_CONSIGNADO.xlsx')

# Função para formatar as colunas de forma correta
def formata_coluna(cols:list) -> list:
    cols_norm = []
    for col in cols:
        cols_norm.append(unidecode(col.lower().replace(' ', '_')).replace('-', ''))

    return cols_norm

retorno.columns = formata_coluna(retorno.columns)
base_ativos.columns = formata_coluna(base_ativos.columns)
remuneracao.columns = formata_coluna(remuneracao.columns)
coopart.columns = formata_coluna(coopart.columns)
obitos.columns = formata_coluna(obitos.columns)

retorno =  retorno.select(pl.all().exclude(['mesclado', 'qtde_rubricas']))
retorno = retorno.with_columns([
    pl.col('cpf').str.slice(-11).alias('cpf')
]).with_columns([
    pl.concat_str(['cpf', 'rubrica'], separator='_').alias('chave')
])
#=====================================================
#======= Regra do calculo da mensalidade dos titulares
#=====================================================
# Orgão do servidor na remuneração: até 07/2026 a coluna se chamava 'cod_org',
# a partir da remuneração de 08/2026 ela passou a se chamar 'codigo_eco'
remuneracao = remuneracao.with_columns([
    ((4 * pl.col('remuneracao_inas')) / 100).round(2).alias('valor_mensalidade'),
    pl.col('cpf').str.replace_all(r'[^\d]', '')
]).with_columns([
    pl.when(pl.col('valor_mensalidade') < 578).then(578)
    .when(pl.col('valor_mensalidade') > 1538).then(1538)
    .otherwise('valor_mensalidade').alias('valor_mensalidade')
]).filter(pl.col('codigo_eco') != '911')

#=========================================================================
#======= Dados cadastrais dos titulares (fonte principal: base de ativos)
#=========================================================================
cadastro_base = base_ativos.filter(pl.col('tipo_segurado') == 'TITULAR').select([
    pl.col('cpf_titular').str.replace_all(r'[^\d]', '').alias('cpf'),
    pl.col('nome_segurado').alias('nome'),
    pl.col('codigo_da_empresa').alias('orgao'),
    pl.col('matricula_titular_8_digitos').alias('matricula'),
    pl.col('situacao_rh').alias('situacao_funcional')
]).unique(subset='cpf')

#===================================================
#======= Regra das rubricas e valor das mensalidades 
#===================================================
base_ativos = base_ativos.with_columns([
    pl.col('cpf_titular').str.replace_all(r'[^\d]', ''),
    pl.col('cpf_segurado').str.replace_all(r'[^\d]', '')
]).select(['cpf_titular', 'cpf_segurado', 'tipo_segurado', 'data_adesao','data_de_cancelamento' , 'codigo_da_empresa', 'matricula_titular_8_digitos', 'idade'])

base_ativos = base_ativos.rename({
    'codigo_da_empresa':'orgao', 'matricula_titular_8_digitos':'matricula'
}).filter(pl.col('orgao') != '911')

base_ativos = base_ativos.with_columns([
    pl.when(pl.col('tipo_segurado') == 'TITULAR').then(pl.lit('41134'))
    .when(pl.col('idade') <= 18).then(pl.lit('41135'))
    .when(pl.col('idade') <= 23).then(pl.lit('41171'))
    .when(pl.col('idade') <= 28).then(pl.lit('41175'))
    .when(pl.col('idade') <= 33).then(pl.lit('41191'))
    .when(pl.col('idade') <= 38).then(pl.lit('41192'))
    .when(pl.col('idade') <= 43).then(pl.lit('41193'))
    .when(pl.col('idade') <= 48).then(pl.lit('41221'))
    .when(pl.col('idade') <= 53).then(pl.lit('41222'))
    .when(pl.col('idade') <= 58).then(pl.lit('41223'))
    .otherwise(pl.lit('41224')).alias('rubrica')
]).with_columns([
    pl.when(pl.col('rubrica') == '41134').then(0)
    .when(pl.col('rubrica') == '41135').then(225)
    .when(pl.col('rubrica') == '41171').then(250)
    .when(pl.col('rubrica') == '41175').then(340)
    .when(pl.col('rubrica') == '41191').then(370)
    .when(pl.col('rubrica') == '41192').then(420)
    .when(pl.col('rubrica') == '41193').then(479)
    .when(pl.col('rubrica') == '41221').then(530)
    .when(pl.col('rubrica') == '41222').then(657)
    .when(pl.col('rubrica') == '41223').then(764)
    .otherwise(853).alias('mensalidade')
])

base_ativos = base_ativos.with_columns([
    pl.concat_str([pl.col('cpf_titular'), pl.col('rubrica')], separator='_').alias('chave')
])
# Tirando alguns casos de beneficiarios que são duplicados por 
base_ativos = base_ativos.unique(subset=['cpf_titular', 'cpf_segurado'])


# Trazendo as mensalidades da remuneração 
base_ativos = (base_ativos.join(
    remuneracao[['cpf', 'valor_mensalidade']], left_on='cpf_titular', right_on='cpf', how='left')
    ).with_columns([
        pl.col('valor_mensalidade').fill_null(578)
    ])

base_ativos = base_ativos.with_columns([
    pl.when(pl.col('mensalidade') == 0).then('valor_mensalidade').otherwise('mensalidade').alias('mensalidade')
]).drop('valor_mensalidade')

base_ativos = base_ativos.filter(pl.col('data_de_cancelamento').is_null())

#=============================
# Regra de calculo da pro rata
#=============================

mes_corrente = date.today().month - 1   # noqa: DTZ011

mes = date.today().replace(day=1, month=mes_corrente)  # noqa: DTZ011
total_dias = calendar.monthrange(mes.year, mes.month)[1] # Total de dias do mes atual

pro_rata = base_ativos.filter(pl.col('data_adesao') >= mes)

pro_rata = pro_rata.with_columns([
    ((pl.col('mensalidade') / total_dias) * ((total_dias + 1) - pl.col('data_adesao').dt.day())).round(2).alias('valor_pro_rata')
]) # Calculo do valor da pro rata com base no total de dias ativo no mês

pro_rata = pro_rata.with_columns([
    (pl.lit('5') + pl.col('rubrica').str.slice(1)).alias('rubrica')
]).with_columns([
    pl.concat_str(['cpf_titular', 'rubrica'], separator='_').alias('chave')
])

# ==============
# Coparticipação
# ============== 

coopart = coopart.with_columns([
    pl.col('cpf').str.replace_all(r'[^\d]', '')
]).rename({'cobranca_copart_titular':'titular', 'cobranca_copart_dependente':'dependente'})

# Dados cadastrais da copart, usados só quando o titular não está na base de ativos
cadastro_copart = coopart.select([
    'cpf', pl.col('nome_titular').alias('nome'), pl.col('emp_cod').alias('orgao'), 'matricula'
]).unique(subset='cpf')

rubrica_coopart = coopart.unpivot(
    index=['emp_cod', 'matricula', 'cpf'],
    on=['dependente', 'titular'],
    variable_name='tipo_beneficiario',
    value_name='valor'
).with_columns([
    pl.when(pl.col('tipo_beneficiario') == 'dependente')
    .then(pl.lit('41144')).otherwise(pl.lit('41136')).alias('rubrica')
]).with_columns([
    pl.concat_str([pl.col('cpf'), pl.col('rubrica')], separator='_').alias('chave')
]).filter(pl.col('valor') > 0)

#================
# Lista de obitos 
#================
cpf_obitos = obitos.select('cpf').unique()

#=========================================
# Buscando rubricas que não foram cobradas
#=========================================
#coparts
coopart_boleto = rubrica_coopart.join(retorno, on='chave', how='anti')

coopart_boleto = coopart_boleto.pivot(
    index=['cpf'],
    values='valor',
    on='tipo_beneficiario'
)
coopart_boleto = coopart_boleto.with_columns([
    pl.col('dependente').fill_null(0),
    pl.col('titular').fill_null(0)
]).rename({'dependente':'copartipacao_dependente', 'titular':'copartipacao_titular'})

#Pro ratas
pro_rata_boleto = pro_rata.join(retorno, on='chave', how='anti')

pro_rata_boleto = pro_rata_boleto.group_by(['cpf_titular', 'tipo_segurado']).agg(
    pl.col('valor_pro_rata').sum()
)

pro_rata_boleto = pro_rata_boleto.pivot(
    index='cpf_titular',
    values='valor_pro_rata',
    on='tipo_segurado'
)

pro_rata_boleto = pro_rata_boleto.with_columns([
    pl.col('TITULAR').fill_null(0),
    pl.col('DEPENDENTE').fill_null(0)
]).rename({'TITULAR':'pro_rata_titular', 'DEPENDENTE':'pro_rata_dependente', 'cpf_titular':'cpf'})

#Mensalidades 
mensalidade_boletos = base_ativos.join(retorno, on='chave', how='anti')

mensalidade_boletos = mensalidade_boletos.group_by(['cpf_titular', 'tipo_segurado']).agg(
    pl.col('mensalidade').sum()
)

mensalidade_boletos = mensalidade_boletos.pivot(
    index='cpf_titular',
    values='mensalidade',
    on='tipo_segurado'
)

mensalidade_boletos = mensalidade_boletos.with_columns([
    pl.col('TITULAR').fill_null(0),
    pl.col('DEPENDENTE').fill_null(0)
]).rename({'TITULAR':'mensalidade_titular', 'DEPENDENTE':'mensalidade_dependente', 'cpf_titular':'cpf'})

#==============
#Ariquivo final
#==============

relatorio_final = (mensalidade_boletos
    .join(coopart_boleto, on='cpf', how='full')
    .with_columns([
        pl.col('cpf').fill_null(pl.col('cpf_right'))
    ]).drop('cpf_right')
    ).fill_null(0)

relatorio_final = (relatorio_final
    .join(pro_rata_boleto, on='cpf', how='full')
    .with_columns([
        pl.col('cpf').fill_null(pl.col('cpf_right'))
    ]).drop('cpf_right')
    ).fill_null(0)

relatorio_final = relatorio_final.with_columns([
    (pl.col('mensalidade_titular') + pl.col('mensalidade_dependente') + pl.col('copartipacao_dependente') + 
     pl.col('copartipacao_titular') + pl.col('pro_rata_titular') + pl.col('pro_rata_dependente') 
     ).alias('total_cobranca')
])

relatorio_final = relatorio_final.filter(pl.col('total_cobranca') > 10)

relatorio_final = relatorio_final.join(cpf_obitos, on='cpf', how='anti') # tirando os beneficiarios que foram a obito

# Nome, orgão e matricula: preferência para a base de ativos, copart só quando não tiver na base
relatorio_final = (relatorio_final
    .join(cadastro_base, on='cpf', how='left')
    .join(cadastro_copart, on='cpf', how='left', suffix='_copart')
    .with_columns([
        pl.coalesce('nome', 'nome_copart').alias('nome'),
        pl.coalesce('orgao', 'orgao_copart').alias('orgao'),
        pl.coalesce('matricula', 'matricula_copart').alias('matricula')
    ]).drop(['nome_copart', 'orgao_copart', 'matricula_copart'])
    )

colunas_cadastro = ['cpf', 'nome', 'orgao', 'matricula', 'situacao_funcional']
relatorio_final = relatorio_final.select(colunas_cadastro + [pl.exclude(colunas_cadastro)])

# Separando o orgão 040 em um arquivo próprio
eh_040 = (pl.col('orgao') == '040').fill_null(False)
hoje = date.today().isoformat()  # noqa: DTZ011

relatorio_final.filter(eh_040).write_excel(f'{OUT_DIR}/relatorio_nao_consignados_040_{hoje}.xlsx')
relatorio_final.filter(~eh_040).write_excel(f'{OUT_DIR}/relatorio_nao_consignados_sem_040_{hoje}.xlsx')
