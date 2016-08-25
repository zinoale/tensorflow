# Copyright 2016 The TensorFlow Authors. All Rights Reserved.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
# ==============================================================================
"""The Wishart distribution class."""

from __future__ import absolute_import
from __future__ import division
from __future__ import print_function

import math
import numpy as np

from tensorflow.contrib.distributions.python.ops import distribution
from tensorflow.contrib.distributions.python.ops import operator_pd_cholesky
from tensorflow.contrib.distributions.python.ops import operator_pd_full
from tensorflow.contrib.framework.python.framework import tensor_util as contrib_tensor_util
from tensorflow.python.framework import constant_op
from tensorflow.python.framework import dtypes
from tensorflow.python.framework import ops
from tensorflow.python.framework import tensor_shape
from tensorflow.python.framework import tensor_util
from tensorflow.python.ops import array_ops
from tensorflow.python.ops import check_ops
from tensorflow.python.ops import control_flow_ops
from tensorflow.python.ops import linalg_ops
from tensorflow.python.ops import math_ops
from tensorflow.python.ops import random_ops


class _WishartOperatorPD(distribution.Distribution):
  """The matrix Wishart distribution on positive definite matrices.

  This distribution is defined by a scalar number of degrees of freedom `df` and
  an instance of `OperatorPDBase`, which provides matrix-free access to a
  symmetric positive definite operator, which defines the scale matrix.

  #### Mathematical details.

  The PDF of this distribution is,

  ```
  f(X) = det(X)^(0.5 (df-k-1)) exp(-0.5 tr[inv(scale) X]) / B(scale, df)
  ```

  where `df >= k` denotes the degrees of freedom, `scale` is a symmetric, pd,
  `k x k` matrix, and the normalizing constant `B(scale, df)` is given by:

  ```
  B(scale, df) = 2^(0.5 df k) |det(scale)|^(0.5 df) Gamma_k(0.5 df)
  ```

  where `Gamma_k` is the multivariate Gamma function.

  #### Examples

  See `WishartFull`, `WishartCholesky` for examples of initializing and using
  this class.
  """

  def __init__(self,
               df,
               scale_operator_pd,
               cholesky_input_output_matrices=False,
               allow_nan_stats=False,
               validate_args=True,
               name='Wishart'):
    """Construct Wishart distributions.

    Args:
      df: `float` or `double` tensor, the degrees of freedom of the
        distribution(s). `df` must be greater than or equal to `k`.
      scale_operator_pd: `float` or `double` instance of `OperatorPDBase`.
      cholesky_input_output_matrices: `Boolean`. Any function which whose input
        or output is a matrix assumes the input is Cholesky and returns a
        Cholesky factored matrix. Example`log_pdf` input takes a Cholesky and
        `sample_n` returns a Cholesky when
        `cholesky_input_output_matrices=True`.
      allow_nan_stats:  `Boolean`, default `False`. If `False`, raise an
        exception if a statistic (e.g., mean, mode) is undefined for any batch
        member. If True, batch members with valid parameters leading to
        undefined statistics will return `NaN` for this statistic.
      validate_args: Whether to validate input with asserts. If `validate_args`
        is `False`, and the inputs are invalid, correct behavior is not
        guaranteed.
      name: The name to give Ops created by the initializer.

    Raises:
      TypeError: if scale is not floating-type
      TypeError: if scale.dtype != df.dtype
      ValueError: if df < k, where scale operator event shape is `(k, k)`
    """
    self._scale_operator_pd = scale_operator_pd
    self._cholesky_input_output_matrices = cholesky_input_output_matrices
    self._allow_nan_stats = allow_nan_stats
    self._validate_args = validate_args
    self._name = name
    with ops.name_scope(name):
      with ops.name_scope('init', values=[df, scale_operator_pd]):
        if not self.dtype.is_floating:
          raise TypeError(
              'scale_operator_pd.dtype=%s is not a floating-point type' %
              self.dtype)
        self._df = ops.convert_to_tensor(df, dtype=self.dtype, name='df')
        contrib_tensor_util.assert_same_float_dtype(
            (self._df, self.scale_operator_pd))
        if (self.scale_operator_pd.get_shape().ndims is None or
            self.scale_operator_pd.get_shape()[-1].value is None):
          self._dimension = math_ops.cast(
              self.scale_operator_pd.vector_space_dimension(),
              dtype=self.dtype, name='dimension')
        else:
          self._dimension = ops.convert_to_tensor(
              self.scale_operator_pd.get_shape()[-1].value,
              dtype=self.dtype, name='dimension')
        df_val = tensor_util.constant_value(self.df)
        dim_val = tensor_util.constant_value(self.dimension)
        if df_val is not None and dim_val is not None:
          df_val = np.asarray(df_val)
          if not df_val.shape: df_val = (df_val,)
          if any(df_val < dim_val):
            raise ValueError(
                'Degrees of freedom (df = %s) cannot be less than dimension of '
                'scale matrix (scale.dimension = %s)'
                % (df_val, dim_val))
        elif self.validate_args:
          assertions = check_ops.assert_less_equal(
              self.dimension, self.df,
              message=('Degrees of freedom (df = %s) cannot be less than '
                       'dimension of scale matrix (scale.dimension = %s)' %
                       (self.dimension, self.df)))
          self._df = control_flow_ops.with_dependencies([assertions], self._df)

  @property
  def inputs(self):
    """Dictionary of inputs provided at initialization."""
    return {'scale_operator_pd': self.scale_operator_pd, 'df': self._df}

  @property
  def allow_nan_stats(self):
    """Boolean describing behavior when a stat is undefined for batch member."""
    return self._allow_nan_stats

  @property
  def validate_args(self):
    """Boolean describing behavior on invalid input."""
    return self._validate_args

  @property
  def name(self):
    """Name prepended to all ops."""
    return self._name

  @property
  def dtype(self):
    """dtype of samples from this distribution."""
    return self.scale_operator_pd.dtype

  @property
  def df(self):
    """Wishart distribution degree(s) of freedom."""
    return self._df

  def scale(self):
    """Wishart distribution scale matrix."""
    if self._cholesky_input_output_matrices:
      return self.scale_operator_pd.sqrt_to_dense()
    else:
      return self.scale_operator_pd.to_dense()

  @property
  def scale_operator_pd(self):
    """Wishart distribution scale matrix as an OperatorPD."""
    return self._scale_operator_pd

  @property
  def cholesky_input_output_matrices(self):
    """Boolean indicating if `Tensor` input/outputs are Cholesky factorized."""
    return self._cholesky_input_output_matrices

  @property
  def dimension(self):
    """Dimension of underlying vector space. The `p` in `R^(p*p)`."""
    return self._dimension

  def is_continuous(self):
    return True

  def is_reparameterized(self):
    return True

  def event_shape(self, name='event_shape'):
    """Shape of a sample from a single distribution as a 1-D int32 `Tensor`."""
    with ops.name_scope(self.name):
      with ops.name_scope(name, values=list(self.inputs.values())):
        s = self.scale_operator_pd.shape()
        return array_ops.slice(s, array_ops.shape(s) - 2, [2])

  def get_event_shape(self):
    """`TensorShape` available at graph construction time."""
    return self.scale_operator_pd.get_shape()[-2:]

  def batch_shape(self, name='batch_shape'):
    """Batch dimensions of this instance as a 1-D int32 `Tensor`."""
    with ops.name_scope(self.name):
      with ops.name_scope(name, values=list(self.inputs.values())):
        return self.scale_operator_pd.batch_shape()

  def get_batch_shape(self):
    """`TensorShape` available at graph construction time."""
    return self.scale_operator_pd.get_batch_shape()

  def prob(self, value, name='prob'):
    """Probability density/mass function."""
    with ops.name_scope(self.name):
      with ops.name_scope(name, values=[value]):
        return math_ops.exp(self.log_prob(value))

  def log_prob(self, x, name='log_prob'):
    """Log of the probability density/mass function.

    Args:
      x: `float` or `double` `Tensor`.
      name: The name to give this op.

    Returns:
      log_prob: a `Tensor` of shape `sample_shape(x) + self.batch_shape` with
        values of type `self.dtype`.
    """
    with ops.name_scope(self.name):
      with ops.name_scope(name, values=[x] + list(self.inputs.values())):
        x = ops.convert_to_tensor(x, name='x')
        contrib_tensor_util.assert_same_float_dtype(
            (self.scale_operator_pd, x))
        if self.cholesky_input_output_matrices:
          x_sqrt = x
        else:
          # Complexity: O(nbk^3)
          x_sqrt = linalg_ops.batch_cholesky(x)

        batch_shape = self.batch_shape()
        event_shape = self.event_shape()
        ndims = array_ops.rank(x_sqrt)
        # sample_ndims = ndims - batch_ndims - event_ndims
        sample_ndims = ndims - array_ops.shape(batch_shape)[0] - 2
        sample_shape = array_ops.slice(
            array_ops.shape(x_sqrt), [0], [sample_ndims])

        # We need to be able to pre-multiply each matrix by its corresponding
        # batch scale matrix.  Since a Distribution Tensor supports multiple
        # samples per batch, this means we need to reshape the input matrix `x`
        # so that the first b dimensions are batch dimensions and the last two
        # are of shape [dimension, dimensions*number_of_samples]. Doing these
        # gymnastics allows us to do a batch_solve.
        #
        # After we're done with sqrt_solve (the batch operation) we need to undo
        # this reshaping so what we're left with is a Tensor partitionable by
        # sample, batch, event dimensions.

        # Complexity: O(nbk^2) since transpose must access every element.
        scale_sqrt_inv_x_sqrt = x_sqrt
        perm = array_ops.concat(0, (math_ops.range(sample_ndims, ndims),
                                    math_ops.range(0, sample_ndims)))
        scale_sqrt_inv_x_sqrt = array_ops.transpose(scale_sqrt_inv_x_sqrt, perm)
        shape = array_ops.concat(
            0, (batch_shape,
                (math_ops.cast(self.dimension, dtype=dtypes.int32), -1)))
        scale_sqrt_inv_x_sqrt = array_ops.reshape(scale_sqrt_inv_x_sqrt, shape)

        # Complexity: O(nbM*k) where M is the complexity of the operator solving
        # a vector system.  E.g., for OperatorPDDiag, each solve is O(k), so
        # this complexity is O(nbk^2). For OperatorPDCholesky, each solve is
        # O(k^2) so this step has complexity O(nbk^3).
        scale_sqrt_inv_x_sqrt = self.scale_operator_pd.sqrt_solve(
            scale_sqrt_inv_x_sqrt)

        # Undo make batch-op ready.
        # Complexity: O(nbk^2)
        shape = array_ops.concat(0, (batch_shape, event_shape, sample_shape))
        scale_sqrt_inv_x_sqrt = array_ops.reshape(scale_sqrt_inv_x_sqrt, shape)
        perm = array_ops.concat(0, (math_ops.range(ndims - sample_ndims, ndims),
                                    math_ops.range(0, ndims - sample_ndims)))
        scale_sqrt_inv_x_sqrt = array_ops.transpose(scale_sqrt_inv_x_sqrt, perm)

        # Write V = SS', X = LL'. Then:
        # tr[inv(V) X] = tr[inv(S)' inv(S) L L']
        #              = tr[inv(S) L L' inv(S)']
        #              = tr[(inv(S) L) (inv(S) L)']
        #              = sum_{ik} (inv(S) L)_{ik}^2
        # The second equality follows from the cyclic permutation property.
        # Complexity: O(nbk^2)
        trace_scale_inv_x = math_ops.reduce_sum(
            math_ops.square(scale_sqrt_inv_x_sqrt),
            reduction_indices=[-2, -1])

        # Complexity: O(nbk)
        half_log_det_x = math_ops.reduce_sum(
            math_ops.log(array_ops.batch_matrix_diag_part(x_sqrt)),
            reduction_indices=[-1])

        # Complexity: O(nbk^2)
        log_prob = ((self.df - self.dimension - 1.) * half_log_det_x -
                    0.5 * trace_scale_inv_x -
                    self.log_normalizing_constant())

        # Set shape hints.
        # Try to merge what we know from the input then what we know from the
        # parameters of this distribution.
        if x.get_shape().ndims is not None:
          log_prob.set_shape(x.get_shape()[:-2])
        if (log_prob.get_shape().ndims is not None and
            self.get_batch_shape().ndims is not None and
            self.get_batch_shape().ndims > 0):
          log_prob.get_shape()[-self.get_batch_shape().ndims:].merge_with(
              self.get_batch_shape())

        return log_prob

  def sample_n(self, n, seed=None, name='sample'):
    # pylint: disable=line-too-long
    """Generate `n` samples.

    Complexity: O(nbk^3)

    The sampling procedure is based on the [Bartlett decomposition](
    https://en.wikipedia.org/wiki/Wishart_distribution#Bartlett_decomposition)
    and [using a Gamma distribution to generate Chi2 random variates](
    https://en.wikipedia.org/wiki/Chi-squared_distribution#Gamma.2C_exponential.2C_and_related_distributions).

    Args:
      n: `Scalar` `Tensor` of type `int32` or `int64`, the number of
        observations to sample.
      seed: Python integer; random number generator seed.
      name: The name of this op.

    Returns:
      samples: a `Tensor` of shape `(n,) + self.batch_shape + self.event_shape`
          with values of type `self.dtype`.
    """
    with ops.name_scope(self.name):
      with ops.name_scope(name, values=[n] + list(self.inputs.values())):
        n = ops.convert_to_tensor(n, name='n')
        if n.dtype != dtypes.int32:
          raise TypeError('n.dtype=%s which is not int32' % n.dtype)
        batch_shape = self.batch_shape()
        event_shape = self.event_shape()
        batch_ndims = array_ops.shape(batch_shape)[0]

        ndims = batch_ndims + 3  # sample_ndims=1, event_ndims=2
        shape = array_ops.concat(0, ((n,), batch_shape, event_shape))

        # Complexity: O(nbk^2)
        x = random_ops.random_normal(shape=shape,
                                     mean=0.,
                                     stddev=1.,
                                     dtype=self.dtype,
                                     seed=seed)

        # Complexity: O(nbk)
        # This parametrization is equivalent to Chi2, i.e.,
        # ChiSquared(k) == Gamma(alpha=k/2, beta=1/2)
        g = random_ops.random_gamma(shape=(n,),
                                    alpha=self._multi_gamma_sequence(
                                        0.5 * self.df, self.dimension),
                                    beta=0.5,
                                    dtype=self.dtype,
                                    seed=seed)

        # Complexity: O(nbk^2)
        x = array_ops.batch_matrix_band_part(x, -1, 0)  # Tri-lower.

        # Complexity: O(nbk)
        x = array_ops.batch_matrix_set_diag(x, math_ops.sqrt(g))

        # Make batch-op ready.
        # Complexity: O(nbk^2)
        perm = array_ops.concat(0, (math_ops.range(1, ndims), (0,)))
        x = array_ops.transpose(x, perm)
        shape = array_ops.concat(0, (batch_shape, (event_shape[0], -1)))
        x = array_ops.reshape(x, shape)

        # Complexity: O(nbM) where M is the complexity of the operator solving a
        # vector system.  E.g., for OperatorPDDiag, each matmul is O(k^2), so
        # this complexity is O(nbk^2). For OperatorPDCholesky, each matmul is
        # O(k^3) so this step has complexity O(nbk^3).
        x = self.scale_operator_pd.sqrt_matmul(x)

        # Undo make batch-op ready.
        # Complexity: O(nbk^2)
        shape = array_ops.concat(0, (batch_shape, event_shape, (n,)))
        x = array_ops.reshape(x, shape)
        perm = array_ops.concat(0, ((ndims-1,), math_ops.range(0, ndims-1)))
        x = array_ops.transpose(x, perm)

        if not self.cholesky_input_output_matrices:
          # Complexity: O(nbk^3)
          x = math_ops.batch_matmul(x, x, adj_y=True)

        # Set shape hints.
        if self.scale_operator_pd.get_shape().ndims is not None:
          x.set_shape(tensor_shape.TensorShape(
              [tensor_util.constant_value(n)] +
              self.scale_operator_pd.get_shape().as_list()))
        elif x.get_shape().ndims is not None:
          x.get_shape()[0].merge_with(
              tensor_shape.TensorDimension(tensor_util.constant_value(n)))

        return x

  def cdf(self, value, name='cdf'):
    """Cumulative distribution function."""
    raise NotImplementedError('cdf is not implemented')

  def log_cdf(self, value, name='log_cdf'):
    """Log CDF."""
    raise NotImplementedError('log_cdf is not implemented')

  def entropy(self, name='entropy'):
    """Entropy of the distribution in nats."""
    half_dp1 = 0.5 * self.dimension + 0.5
    half_df = 0.5 * self.df
    return (self.dimension * (half_df + half_dp1 * math.log(2.)) +
            half_dp1 * self.scale_operator_pd.log_det() +
            self._multi_lgamma(half_df, self.dimension) +
            (half_dp1 - half_df) * self._multi_digamma(half_df, self.dimension))

  def mean(self, name='mean'):
    """Mean of the distribution."""
    with ops.name_scope(self.name):
      with ops.name_scope(name, values=list(self.inputs.values())):
        if self.cholesky_input_output_matrices:
          return math_ops.sqrt(self.df) * self.scale_operator_pd.sqrt_to_dense()
        else:
          return self.df * self.scale_operator_pd.to_dense()

  def mode(self, name='mode'):
    """Mode of the distribution."""
    with ops.name_scope(self.name):
      with ops.name_scope(name, values=list(self.inputs.values())):
        s = self.df - self.dimension - 1.
        s = math_ops.select(
            math_ops.less(s, 0.),
            constant_op.constant(float('NaN'), dtype=self.dtype, name='nan'),
            s)
        if self.cholesky_input_output_matrices:
          return math_ops.sqrt(s) * self.scale_operator_pd.sqrt_to_dense()
        else:
          return s * self.scale_operator_pd.to_dense()

  def std(self, name='std'):
    """Standard deviation of the Wishart distribution."""
    with ops.name_scope(self.name):
      with ops.name_scope(name, values=list(self.inputs.values())):
        if self.cholesky_input_output_matrices:
          raise ValueError(
              'Computing std. dev. when is cholesky_input_output_matrices=True '
              'does not make sense.')
        return linalg_ops.batch_cholesky(self.variance())

  def variance(self, name='variance'):
    """Variance of the Wishart distribution.

    This function should not be confused with the covariance of the Wishart. The
    covariance matrix would have shape `q x q` where,
    `q = dimension * (dimension+1) / 2`
    and having elements corresponding to some mapping from a lower-triangular
    matrix to a vector-space.

    This function returns the diagonal of the Covariance matrix but shaped
    as a `dimension x dimension` matrix.

    Args:
      name: The name of this op.

    Returns:
      variance: `Tensor` of dtype `self.dtype`.
    """
    with ops.name_scope(self.name):
      with ops.name_scope(name, values=list(self.inputs.values())):
        x = math_ops.sqrt(self.df) * self.scale_operator_pd.to_dense()
        d = array_ops.expand_dims(array_ops.batch_matrix_diag_part(x), -1)
        v = math_ops.square(x) + math_ops.batch_matmul(d, d, adj_y=True)
        if self.cholesky_input_output_matrices:
          return linalg_ops.batch_cholesky(v)
        else:
          return v

  def mean_log_det(self, name='mean_log_det'):
    """Computes E[log(det(X))] under this Wishart distribution."""
    with ops.name_scope(self.name):
      with ops.name_scope(name, values=list(self.inputs.values())):
        return (self._multi_digamma(0.5 * self.df, self.dimension) +
                self.dimension * math.log(2.) +
                self.scale_operator_pd.log_det())

  def log_normalizing_constant(self, name='log_normalizing_constant'):
    """Computes the log normalizing constant, log(Z)."""
    with ops.name_scope(self.name):
      with ops.name_scope(name, values=list(self.inputs.values())):
        return (self.df * self.scale_operator_pd.sqrt_log_det() +
                0.5 * self.df * self.dimension * math.log(2.) +
                self._multi_lgamma(0.5 * self.df, self.dimension))

  def _multi_gamma_sequence(self, a, p, name='multi_gamma_sequence'):
    """Creates sequence used in multivariate (di)gamma; shape = shape(a)+[p]."""
    with ops.name_scope(self.name):
      with ops.name_scope(name, values=[a, p]):
        # Linspace only takes scalars, so we'll add in the offset afterwards.
        seq = math_ops.linspace(
            constant_op.constant(0., dtype=self.dtype),
            0.5 - 0.5 * p,
            math_ops.cast(p, dtypes.int32))
        return seq + array_ops.expand_dims(a, [-1])

  def _multi_lgamma(self, a, p, name='multi_lgamma'):
    """Computes the log multivariate gamma function; log(Gamma_p(a))."""
    with ops.name_scope(self.name):
      with ops.name_scope(name, values=[a, p]):
        seq = self._multi_gamma_sequence(a, p)
        return (0.25 * p * (p - 1.) * math.log(math.pi) +
                math_ops.reduce_sum(math_ops.lgamma(seq),
                                    reduction_indices=(-1,)))

  def _multi_digamma(self, a, p, name='multi_digamma'):
    """Computes the multivariate digamma function; Psi_p(a)."""
    with ops.name_scope(self.name):
      with ops.name_scope(name, values=[a, p]):
        seq = self._multi_gamma_sequence(a, p)
        return math_ops.reduce_sum(math_ops.digamma(seq),
                                   reduction_indices=(-1,))


class WishartCholesky(_WishartOperatorPD):
  """The matrix Wishart distribution on positive definite matrices.

  This distribution is defined by a scalar degrees of freedom `df` and a
  lower, triangular Cholesky factor which characterizes the scale matrix.

  Using WishartCholesky is a constant-time improvement over WishartFull. It
  saves an O(nbk^3) operation, i.e., a matrix-product operation for sampling
  and a Cholesky factorization in log_prob. For most use-cases it often saves
  another O(nbk^3) operation since most uses of Wishart will also use the
  Cholesky factorization.

  #### Mathematical details.

  The PDF of this distribution is,

  ```
  f(X) = det(X)^(0.5 (df-k-1)) exp(-0.5 tr[inv(scale) X]) / B(scale, df)
  ```

  where `df >= k` denotes the degrees of freedom, `scale` is a symmetric, pd,
  `k x k` matrix, and the normalizing constant `B(scale, df)` is given by:

  ```
  B(scale, df) = 2^(0.5 df k) |det(scale)|^(0.5 df) Gamma_k(0.5 df)
  ```

  where `Gamma_k` is the multivariate Gamma function.


  #### Examples

  ```python
  # Initialize a single 3x3 Wishart with Cholesky factored scale matrix and 5
  # degrees-of-freedom.(*)
  df = 5
  chol_scale = tf.cholesky(...)  # Shape is [3, 3].
  dist = tf.contrib.distributions.WishartCholesky(df=df, scale=chol_scale)

  # Evaluate this on an observation in R^3, returning a scalar.
  x = ... # A 3x3 positive definite matrix.
  dist.pdf(x)  # Shape is [], a scalar.

  # Evaluate this on a two observations, each in R^{3x3}, returning a length two
  # Tensor.
  x = [x0, x1]  # Shape is [2, 3, 3].
  dist.pdf(x)  # Shape is [2].

  # Initialize two 3x3 Wisharts with Cholesky factored scale matrices.
  df = [5, 4]
  chol_scale = tf.batch_cholesky(...)  # Shape is [2, 3, 3].
  dist = tf.contrib.distributions.WishartCholesky(df=df, scale=chol_scale)

  # Evaluate this on four observations.
  x = [[x0, x1], [x2, x3]]  # Shape is [2, 2, 3, 3].
  dist.pdf(x)  # Shape is [2, 2].

  # (*) - To efficiently create a trainable covariance matrix, see the example
  #   in tf.contrib.distributions.batch_matrix_diag_transform.
  ```

  """

  def __init__(self,
               df,
               scale,
               cholesky_input_output_matrices=False,
               allow_nan_stats=False,
               validate_args=True,
               name='Wishart'):
    """Construct Wishart distributions.

    Args:
      df: `float` or `double` `Tensor`. Degrees of freedom, must be greater than
        or equal to dimension of the scale matrix.
      scale: `float` or `double` `Tensor`. The Cholesky factorization of
        the symmetric positive definite scale matrix of the distribution.
      cholesky_input_output_matrices: `Boolean`. Any function which whose input
        or output is a matrix assumes the input is Cholesky and returns a
        Cholesky factored matrix. Example`log_pdf` input takes a Cholesky and
        `sample_n` returns a Cholesky when
        `cholesky_input_output_matrices=True`.
      allow_nan_stats:  `Boolean`, default `False`. If `False`, raise an
        exception if a statistic (e.g., mean, mode) is undefined for any batch
        member. If True, batch members with valid parameters leading to
        undefined statistics will return `NaN` for this statistic.
      validate_args: Whether to validate input with asserts. If `validate_args`
        is `False`, and the inputs are invalid, correct behavior is not
        guaranteed.
      name: The name scope to give class member ops.
    """
    super(WishartCholesky, self).__init__(
        df=df,
        scale_operator_pd=operator_pd_cholesky.OperatorPDCholesky(
            scale, verify_pd=validate_args),
        cholesky_input_output_matrices=cholesky_input_output_matrices,
        allow_nan_stats=allow_nan_stats,
        validate_args=validate_args,
        name=name)


class WishartFull(_WishartOperatorPD):
  """The matrix Wishart distribution on positive definite matrices.

  This distribution is defined by a scalar degrees of freedom `df` and a
  symmetric, positive definite scale matrix.

  Evaluation of the pdf, determinant, and sampling are all `O(k^3)` operations
  where `(k, k)` is the event space shape.

  #### Mathematical details.

  The PDF of this distribution is,

  ```
  f(X) = det(X)^(0.5 (df-k-1)) exp(-0.5 tr[inv(scale) X]) / B(scale, df)
  ```

  where `df >= k` denotes the degrees of freedom, `scale` is a symmetric, pd,
  `k x k` matrix, and the normalizing constant `B(scale, df)` is given by:

  ```
  B(scale, df) = 2^(0.5 df k) |det(scale)|^(0.5 df) Gamma_k(0.5 df)
  ```

  where `Gamma_k` is the multivariate Gamma function.

  #### Examples

  ```python
  # Initialize a single 3x3 Wishart with Full factored scale matrix and 5
  # degrees-of-freedom.(*)
  df = 5
  scale = ...  # Shape is [3, 3]; positive definite.
  dist = tf.contrib.distributions.WishartFull(df=df, scale=scale)

  # Evaluate this on an observation in R^3, returning a scalar.
  x = ... # A 3x3 positive definite matrix.
  dist.pdf(x)  # Shape is [], a scalar.

  # Evaluate this on a two observations, each in R^{3x3}, returning a length two
  # Tensor.
  x = [x0, x1]  # Shape is [2, 3, 3].
  dist.pdf(x)  # Shape is [2].

  # Initialize two 3x3 Wisharts with Full factored scale matrices.
  df = [5, 4]
  scale = ...  # Shape is [2, 3, 3].
  dist = tf.contrib.distributions.WishartFull(df=df, scale=scale)

  # Evaluate this on four observations.
  x = [[x0, x1], [x2, x3]]  # Shape is [2, 2, 3, 3]; xi is positive definite.
  dist.pdf(x)  # Shape is [2, 2].

  # (*) - To efficiently create a trainable covariance matrix, see the example
  #   in tf.contrib.distributions.batch_matrix_diag_transform.
  ```

  """

  def __init__(self,
               df,
               scale,
               cholesky_input_output_matrices=False,
               allow_nan_stats=False,
               validate_args=True,
               name='Wishart'):
    """Construct Wishart distributions.

    Args:
      df: `float` or `double` `Tensor`. Degrees of freedom, must be greater than
        or equal to dimension of the scale matrix.
      scale: `float` or `double` `Tensor`. The symmetric positive definite
        scale matrix of the distribution.
      cholesky_input_output_matrices: `Boolean`. Any function which whose input
        or output is a matrix assumes the input is Cholesky and returns a
        Cholesky factored matrix. Example`log_pdf` input takes a Cholesky and
        `sample_n` returns a Cholesky when
        `cholesky_input_output_matrices=True`.
      allow_nan_stats:  `Boolean`, default `False`. If `False`, raise an
        exception if a statistic (e.g., mean, mode) is undefined for any batch
        member. If True, batch members with valid parameters leading to
        undefined statistics will return `NaN` for this statistic.
      validate_args: Whether to validate input with asserts. If `validate_args`
        is `False`, and the inputs are invalid, correct behavior is not
        guaranteed.
      name: The name scope to give class member ops.
    """
    super(WishartFull, self).__init__(
        df=df,
        scale_operator_pd=operator_pd_full.OperatorPDFull(
            scale, verify_pd=validate_args),
        cholesky_input_output_matrices=cholesky_input_output_matrices,
        allow_nan_stats=allow_nan_stats,
        validate_args=validate_args,
        name=name)
