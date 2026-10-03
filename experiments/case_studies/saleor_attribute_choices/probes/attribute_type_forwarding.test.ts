import { type ApolloClient } from '@apollo/client';
import { AttributeInputTypeEnum } from '@dashboard/graphql';

import { Condition } from '../../FilterElement/Condition';
import { ExpressionValue, FilterElement } from '../../FilterElement/FilterElement';
import { AttributeQueryVarsBuilder } from './AttributeQueryVarsBuilder';

describe('RQ2 behavior admission: attribute type forwarding', () => {
  it('routes a BOOLEAN selected attribute to the static choices branch', async () => {
    const query = jest.fn();
    const client = { query } as unknown as ApolloClient<unknown>;
    const element = new FilterElement(
      new ExpressionValue('attribute', 'Attribute', 'attribute'),
      Condition.createEmpty(),
      false,
      undefined,
      new ExpressionValue('is-visible', 'Is visible', AttributeInputTypeEnum.BOOLEAN),
    );

    const handler = new AttributeQueryVarsBuilder().createOptionFetcher(client, '', element);
    await expect(handler.fetch()).resolves.toEqual([
      { label: 'Yes', value: 'true', slug: 'true' },
      { label: 'No', value: 'false', slug: 'false' },
    ]);
    expect(query).not.toHaveBeenCalled();
  });
});
