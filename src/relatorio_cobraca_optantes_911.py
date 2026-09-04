from datetime import date
from pathlib import Path

import polars as pl
from unidecode import unidecode

BASE_DIR = Path.cwd()  / 'data_input' / 'pc_optantes' / '07'
OUT_DIR = Path.cwd()  / 'data_output' 

optantes = pl.read_excel(f'{BASE_DIR}/OPTANTES.xlsx', sheet_name='Export') 
base_ativos = pl.read_excel(f'{BASE_DIR}/BASE_ATIVOS_E_CANCELADOS.xlsx')
remuneracao = pl.read_excel(f'{BASE_DIR}/REMUNERACAO.xlsx')
dados_copart = pl.read_excel(f'{BASE_DIR}/COPARTICIPACAO_BOLETO.xlsx', sheet_name='titular_sem_vinculo')

def formata_coluna(cols:list) -> list:
    cols_norm = []
    for col in cols:
        cols_norm.append(unidecode(col.lower().replace(' ', '_')))

    return cols_norm

optantes.columns = formata_coluna(optantes.columns)
base_ativos.columns = formata_coluna(base_ativos.columns)
remuneracao.columns = formata_coluna(remuneracao.columns)
dados_copart.columns = formata_coluna(dados_copart.columns)

#======================
#Regra de 4% do salário.
#======================
remuneracao = remuneracao.with_columns([
    ((4 * pl.col('remuneracao_inas')) / 100).round(2).alias('valor_mensalidade')
]).with_columns([
    pl.when(pl.col('valor_mensalidade') < 578).then(578)
    .when(pl.col('valor_mensalidade') > 1538).then(1538)
    .otherwise('valor_mensalidade').alias('valor_mensalidade')
])

dados_gerais_optantes = (optantes.join(
    base_ativos[['cpf_titular', 'cpf_segurado', 'nome_segurado', 'tipo_segurado' , 'codigo_da_empresa', 'matricula_titular_8_digitos', 'idade']], 
    on=['cpf_titular', 'cpf_segurado'], 
    how='inner')
    .drop(['nome_segurado_right'])
    .sort(by='cpf_segurado')
    .rename({'matricula_titular_8_digitos':'matricula'})
    .with_columns([pl.col('codigo_da_empresa').cast(pl.Int64)])
    )

#=====================
#Adicionando rubrica e valor relacionado
#=====================
dados_gerais_optantes = dados_gerais_optantes.with_columns([
    pl.when(pl.col('tipo_segurado') == 'TITULAR').then(pl.lit('41134'))
    .when(pl.col('idade') <= 18).then(pl.lit('41135'))
    .when(pl.col('idade') <= 23).then(pl.lit('41171'))
    .when(pl.col('idade') <= 28).then(pl.lit('41175'))
    .when(pl.col('idade') <= 33).then(pl.lit('41191'))
    .when(pl.col('idade') <= 38).then(pl.lit('41192'))
    .when(pl.col('idade') <= 43).then(pl.lit('41193'))
    .when(pl.col('idade') <= 48).then(pl.lit('41121'))
    .when(pl.col('idade') <= 53).then(pl.lit('41122'))
    .when(pl.col('idade') <= 58).then(pl.lit('41123'))
    .otherwise(pl.lit('41224')).alias('rubrica')
]).with_columns([
    pl.when(pl.col('rubrica') == '41134').then(0)
    .when(pl.col('rubrica') == '41135').then(225)
    .when(pl.col('rubrica') == '41171').then(250)
    .when(pl.col('rubrica') == '41175').then(340)
    .when(pl.col('rubrica') == '41191').then(370)
    .when(pl.col('rubrica') == '41192').then(420)
    .when(pl.col('rubrica') == '41193').then(479)
    .when(pl.col('rubrica') == '41121').then(530)
    .when(pl.col('rubrica') == '41122').then(657)
    .when(pl.col('rubrica') == '41123').then(764)
    .otherwise(853).alias('mensalidade')
]).with_columns([
    pl.when(pl.col('situacao_funcional') == 'Titular optante anterior')
    .then('mensalidade')
    .otherwise(pl.col('mensalidade') * 2).alias('mensalidade')
])

group1 = dados_gerais_optantes.group_by(['cpf_titular', 'tipo_segurado', 'codigo_da_empresa', 'matricula', 'situacao_funcional']).agg(
    pl.col('mensalidade').sum()
)

#=====================
#Pivotando a tabale para que os valores da coluna tipo_segurado virem colunas com os valores da coluna mensalidade.
#=====================
pivot = group1.pivot(
    values='mensalidade',
    index=['cpf_titular', 'codigo_da_empresa', 'matricula', 'situacao_funcional'],
    on='tipo_segurado'
).fill_null(0)

#====================
#Reagrupando pelo cpf titular, para que linhas de DEPENDENTES sumam e excluindo PC.
#====================
group2 = pivot.group_by('cpf_titular', 'codigo_da_empresa', 'matricula', 'situacao_funcional').agg(
    pl.col('TITULAR').sum().alias('mensalidade_titular'),
    pl.col('DEPENDENTE').sum().alias('mensalidade_dependente')
).filter(pl.col('codigo_da_empresa') != 911)

#===================
#Adicionando valor da mensalidade do titular com regra dos optantes e optantes anteriores
#===================
valores_mensalidade = (group2.join(remuneracao[['cpf', 'valor_mensalidade']], 
            left_on='cpf_titular', right_on='cpf', how='left')
      ).unique(subset='cpf_titular').fill_null(578)

final_optantes =  valores_mensalidade.with_columns([
    pl.when(pl.col('situacao_funcional') == 'Titular optante anterior').then('valor_mensalidade')
    .otherwise(pl.col('valor_mensalidade') + 578).alias('mensalidade_titular')
]).select(pl.all().exclude('valor_mensalidade'))

final_optantes =  final_optantes.join(optantes[['cpf_segurado', 'nome_segurado']],
                                        left_on='cpf_titular',
                                        right_on='cpf_segurado', how='inner')
#===================
#Buscando os dados de copart
#===================
dados_copart = dados_copart.select('cpf', 'nome_titular','emp_cod', 'matricula', 'cobranca_copart_titular', 'cobranca_copart_dependente')
dados_copart = dados_copart.with_columns([
    (pl.col('cobranca_copart_titular') + pl.col('cobranca_copart_dependente')).alias('total_cobranca'),
    pl.col('emp_cod').cast(pl.Int64)
]).filter(pl.col('total_cobranca') > 10).drop('total_cobranca')

#===================
#Resultado final
#===================
excel_maida = (dados_copart
    .join(final_optantes, left_on='cpf', right_on='cpf_titular', how='full')
    .with_columns([
        pl.coalesce(pl.col('cpf'), pl.col('cpf_titular')).alias('cpf_titular'),
        pl.coalesce(pl.col('nome_titular'), pl.col('nome_segurado')).alias('nome'),
        pl.coalesce(pl.col('emp_cod'), pl.col('codigo_da_empresa')).alias('empresa'),
        pl.coalesce(pl.col('matricula'), pl.col('matricula_right')).alias('matricula'),
        pl.lit('Boleto').alias('forma_cobranca'),
        pl.col('mensalidade_titular').fill_null(0),
        pl.col('mensalidade_dependente').fill_null(0),
        pl.col('cobranca_copart_titular').fill_null(0),
        pl.col('cobranca_copart_dependente').fill_null(0),
        pl.col('situacao_funcional').fill_null('-')
    ])).select(['cpf_titular', 'nome', 'situacao_funcional', 'empresa', 'matricula', 
                'forma_cobranca', 'mensalidade_titular', 'mensalidade_dependente', 'cobranca_copart_titular', 'cobranca_copart_dependente'])

excel_maida = excel_maida.with_columns([
    (pl.col('mensalidade_titular') + pl.col('mensalidade_dependente') + pl.col('cobranca_copart_titular') + pl.col('cobranca_copart_dependente')).alias('Total_cobranca')
])

execoes = excel_maida.filter((pl.col('situacao_funcional') == '-') & (pl.col('empresa') != 911))

excel_maida = excel_maida.filter(~((pl.col('situacao_funcional') == '-') & (pl.col('empresa') != 911)))


execoes.write_parquet(f'{OUT_DIR}/execoes_boletos{date.today().isoformat()}.parquet')  # noqa: DTZ011
excel_maida.write_excel(f'{OUT_DIR}/relatorio_cobranca_pc_optantes{date.today().isoformat()}.xlsx', float_precision=2)  # noqa: DTZ011
